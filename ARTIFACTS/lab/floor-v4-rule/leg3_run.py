#!/usr/bin/env python3
"""Leg (iii) runner for the l1-floor-v4 rule: v4 offline decisions md5 (A) vs osb-ce1's whole-span decisions md5 (B). PAPER, decisions only. NOT RUN by the PR that adds it:
the manager runs it later as ONE MiScusi job at the exact install tree (including 8288ba12). The comparison rule is FIXED in tools/floor_v4_leg3_compare.py and LEG3-README.md
(not changed here); this file only feeds it.

  /data/mal/venv/bin/python ARTIFACTS/lab/floor-v4-rule/leg3_run.py [--manifest /data2/mal/scratch/floor-open-sim/manifests/CE1.json] [--work-root DIR]

What it does
  1. Span. The CE1 manifest (osb_follow.load_manifest; path guard and manifest pin as osb_board does) gives start_hour, seed_from_hour, tape_dir, stakes, send and out_root. The last hour
     the book has followed is out_root/status.json `last_hour` / `last_hour_end_ms` (the driver writes it after each hour of the ACTIVE run; refused while the book is catching up).
  2. B = osb_follow.decisions_md5_from_rows over the rows of out_root/book/ledger (osb_follow.decision_rows), whole span; never md5-by-hour.jsonl. The rows are read only to be hashed
     and counted. B is accepted only if status.json (last_hour, last_hour_end_ms, run) and the book link are unchanged around the read and two consecutive full reads give the same md5 (else retried, then exit 2).
  3. A = the v4 rule offline over the SAME hours from start_hour with the SAME seed_from_hour: tools/floor_lane_host.make_lane_host(rule_id l1-floor-v4, q_star_lamports 1_000_000_000 (int), tp 1.3,
     live=False, lane guard as the manifest says), built and driven as osb_follow does for CE1 (seed the pool table, host.resume(), one host.run(hour, hour + 1 h) per tape hour, same stakes,
     send and stack config; the engine is the plain ArenaEngine the live host uses; GapRangeEngine with gap_exclusion on is documented byte-identical). It writes only into a fresh mkdtemp
     directory under --work-root (removed at the end), never into a live or book dir. The tape is only read.
  4. floor_v4_leg3_compare.normalized_md5(ledger rows, v4 entry rows) vs B, leg3_equal.
  5. Prints ONLY: git HEAD, hours (first, last, count), seed_from_hour, B, A, EQUAL/UNEQUAL, the distinct-position counts of each side (never a row count), the exit code. No price, fill, P&L, exit_reason, TP/SL mix or row.
Exit codes: 0 equal, 1 unequal, 2 setup error (span, seed, manifest, pins, paths, tape hour missing, B unstable).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for p in (str(REPO), str(REPO / "scripts" / "research")):
    if p not in sys.path:
        sys.path.insert(0, p)

import floor_tape_paths as TP  # noqa: E402
import osb_follow as O  # noqa: E402
from tools import arena_rules as R  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402
from tools import floor_v4_leg3_compare as C  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.arena_paper import ArenaEngine  # noqa: E402
from tools.arena_stack_v2 import stack_config_for  # noqa: E402
from tools.arena_state import WARM_MIN_DEFAULT  # noqa: E402

DEFAULT_MANIFEST = "/data2/mal/scratch/floor-open-sim/manifests/CE1.json"
DEFAULT_WORK_ROOT = "/data2/mal/scratch/floor-v4-leg3"
TP_V4 = 1.3
Q_STAR_V4 = 1_000_000_000
HOUR_MS = O.HOUR_MS
B_TRIES = 6
B_RETRY_S = 30
EXIT_EQUAL, EXIT_UNEQUAL, EXIT_SETUP = 0, 1, 2


class SetupError(Exception):
    """exit 2"""


CE1_ID = "osb-ce1"
CE1_OVERRIDES = {"Q_STAR": 1.0e9, "TP": 1.3}
CE1_MANIFEST_SHA = "5fe6053311e4dce37f8affa21b7bb9bed9d4cf930ed513d935da7ffaf6c7767a"  # canonical manifest sha256 of osb-ce1, DEC-035 Amendment 9 book-list record 2026-11-16
WORK_ALLOWED = ("/data2/mal/scratch",)
WORK_REFUSED = ("/var/lib", "/etc")


def check_ce1_shape(m, pin=None):
    """Exit 2 unless the manifest has exactly osb-ce1's shape: the only shape for which every GapRangeEngine override hands off to ArenaEngine (QP on PR 1505)."""
    checks = [("book_id is osb-ce1", m.get("book_id") == CE1_ID),
              ("host is lane", m.get("host") == "lane"),
              ("rule_overrides are exactly Q_STAR 1e9 and TP 1.3", m.get("rule_overrides") == CE1_OVERRIDES),
              ("gap_exclusion absent or on", m.get("gap_exclusion", "on") == "on"),
              ("no pricing_lat_entry_s (no latency twin)", "pricing_lat_entry_s" not in m),
              ("no twin_of", "twin_of" not in m)]
    checks.append(("canonical manifest sha256 is osb-ce1's (DEC-035 Am.9)", O.manifest_sha256(m) == (pin or CE1_MANIFEST_SHA)))
    for name, ok in checks:
        if not ok:
            raise SetupError("manifest shape: check failed: %s" % name)


def check_work_root(work_root, m, allowed=WORK_ALLOWED, roots=None):
    """--work-root must sit under an allowed scratch root and under none of: the arena output root, /var/lib, /etc, the book's out_root, the tape_dir. The run uses a fresh mkdtemp inside it."""
    w = os.path.realpath(work_root)
    refused = tuple(WORK_REFUSED) + (R.ARENA_OUTPUT_ROOT, os.path.realpath(m["out_root"]), os.path.realpath(m["tape_dir"]))
    if any(TP._under(w, os.path.realpath(r)) or TP._under(os.path.realpath(r), w) for r in refused):
        raise SetupError("work root refused")
    if TP.refusal(w, tuple(allowed) if roots is None else tuple(roots)):
        raise SetupError("work root refused by the tape path guard")
    if not any(TP._under(w, os.path.realpath(a)) for a in allowed):
        raise SetupError("work root is not under an allowed scratch root")


def git_head():
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def read_status(out_root):
    try:
        d = json.loads((Path(out_root) / "status.json").read_text())
    except (OSError, ValueError):
        raise SetupError("status.json unreadable")
    if not isinstance(d, dict):
        raise SetupError("status.json is not an object")
    return d


def span_of(m, status):
    """(first_ms, last_end_ms, first_key, last_key, count) of the hours the book has followed, from its manifest and status. Refused while catching up or empty."""
    if status.get("catching_up_to_stored_hours") or status.get("active") is not True:
        raise SetupError("book is not active (catching up or no verified hour)")
    end, last = status.get("last_hour_end_ms"), status.get("last_hour")
    if isinstance(end, bool) or not isinstance(end, int) or not isinstance(last, str):
        raise SetupError("status.json has no last hour")
    first = H.parse_hour(m["start_hour"])
    if end <= first or (end - first) % HOUR_MS:
        raise SetupError("no whole hour between start_hour and the last hour")
    if O.hour_key(end - HOUR_MS) != last:
        raise SetupError("status last_hour and last_hour_end_ms disagree")
    return first, end, m["start_hour"], last, (end - first) // HOUR_MS


def read_b(out_root, roots, sleep_fn=time.sleep, tries=B_TRIES, retry_s=B_RETRY_S):
    """(B, n_rows, rows, status): the whole-span decisions md5 of the active run's ledger rows. Accepted only when ALL hold, read in one window:
      - status.json `ledger_md5` is a 32-hex value, equal in the status read before and after, and equal to osb_follow.tree_md5(<book target>/ledger) computed in that window (the driver writes it
        after the hour is done, so a row written for the NEXT hour before the status update makes the tree md5 differ: retry);
      - status last_hour, last_hour_end_ms and run are unchanged, the `book` link target is unchanged, and basename(target) == status `run`;
      - two consecutive full reads of the rows give the same md5.
    Anything else (a torn last line, an OSError, a ValueError from the tree md5) is a retry, then SetupError (exit 2), never an uncaught error (exit 1).
    status.json `decisions_md5` is NOT used: books run from older commits (osb-ce1 at 457795f4) do not write that key; `ledger_md5` is written there and tree_md5 is byte-identical at both commits."""
    out_root = Path(out_root)
    link = out_root / "book"

    def stamp(st, target):
        return (st.get("last_hour"), st.get("last_hour_end_ms"), st.get("run"), st.get("ledger_md5"), target)

    for attempt in range(tries):
        try:
            s1 = read_status(out_root)
            if not link.is_symlink():
                raise SetupError("no book link")
            target = os.path.realpath(link)
            why = TP.refusal(target, roots)
            if why:
                raise SetupError("ledger path refused")
            ledger = Path(target) / "ledger"
            lm = s1.get("ledger_md5")
            ok = isinstance(lm, str) and re.fullmatch(r"[0-9a-f]{32}", lm) is not None and os.path.basename(target) == s1.get("run")
            tree = O.tree_md5(ledger) if ok else None
            rows = list(O.decision_rows(ledger))
            b1 = O.decisions_md5_from_rows(rows)
            rows2 = list(O.decision_rows(ledger))
            b2 = O.decisions_md5_from_rows(rows2)
            tree2 = O.tree_md5(ledger) if ok else None
            s2 = read_status(out_root)
        except (OSError, ValueError):  # a torn or half-written line, a file moving under us: retry
            ok = False
        else:
            if ok and tree == tree2 == lm and stamp(s1, target) == stamp(s2, os.path.realpath(link)) and b1 == b2 and any(r.get("kind") == "open" for r in rows):
                return b1, len(rows), rows, s2
        if attempt + 1 < tries:
            sleep_fn(retry_s)
    raise SetupError("the book's decisions md5 was not stable after %d reads (status, ledger md5, link and two equal reads)" % tries)


def run_v4(m, last_end_ms, workdir, table_mode=None, engine_kw=None):
    """The v4 rule offline over [start_hour, last_end_ms), hour by hour as osb_follow does. -> (ledger rows, v4 decision-log entry rows). Writes only under workdir."""
    start_ms = H.parse_hour(m["start_hour"])
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    ledger, registry = str(workdir / "ledger"), str(workdir / "bots.json")
    warm_ms = start_ms - WARM_MIN_DEFAULT * R.MIN_MS
    rule = H._load_rule(H.RULE_V4, Q_STAR_V4, TP_V4)
    os.environ["MAL_ARENA_TEST"] = "1"  # the scratch registry alone (as floor_replay_proof_reg.scratch_register); the real registry is never opened
    try:
        R.register_rules([rule], registry, H.FROZEN_SHA, start_ms, now_ms=start_ms - 1, verify_sha=False)
    finally:
        os.environ.pop("MAL_ARENA_TEST", None)
    cfg = dataclasses.replace(stack_config_for(2, R.DEFAULT_FEED_DELAY_SLOTS), send=m["send_lamports"], stakes_sol=tuple(float(x) for x in m["stakes_sol"]))
    engine = ArenaEngine(ledger, registry, cfg=cfg, **(engine_kw or {}))
    lane = m.get("lane", {})
    host = H.make_lane_host(root=str(workdir), ledger_root=ledger, registry=registry, tape_dir=m["tape_dir"], clock_start_ms=start_ms, live=False, binding=None, start_ms=warm_ms,
                            lookback_s=int(lane.get("gap_lookback_s", H.LOOKBACK_MIN_S)), table_mode=table_mode, engine=engine,
                            status_span_no_loss=bool(lane.get("status_span_no_loss", False)), cheap_restart=bool(lane.get("cheap_restart", False)),
                            rule_id=H.RULE_V4, q_star_lamports=Q_STAR_V4, tp=TP_V4)
    H.rebuild_table_since(host.table, m["tape_dir"], warm_ms, H.parse_hour(m["seed_from_hour"]), str(workdir / "seed-tmp"))
    host.resume()
    cursor = start_ms
    while cursor < last_end_ms:
        host.run(cursor, cursor + HOUR_MS)
        cursor += HOUR_MS
    ledger_rows = list(O.decision_rows(Path(ledger)))
    entries = [r for r in DecisionLog(str(workdir)).rows(H.RULE_V4) if r.get("kind") == "entry"]
    return ledger_rows, entries


def check_tape_hours(m, first_ms, last_end_ms, roots):
    """every hour in [seed_from_hour, last_end) must have a tape file: the seed and warm-up hours too (rebuild_table_since skips a missing one silently, which would seed another table: a spurious UNEQUAL that cannot be re-run)"""
    TP.require_files_ok(m["tape_dir"], roots)
    cursor = H.parse_hour(m["seed_from_hour"])
    if cursor > first_ms:
        raise SetupError("seed_from_hour is after start_hour")
    while cursor < last_end_ms:
        if O.hour_file(m["tape_dir"], cursor) is None:
            raise SetupError("a tape hour of the seed, warm-up or span is missing")
        cursor += HOUR_MS


def distinct_positions(rows):
    """the number of distinct position_ids among the open / trade / excluded rows (what decisions_md5_from_rows hashes); never a row count"""
    return len({r["position_id"] for r in rows if r.get("kind") in ("open", "trade", "excluded") and isinstance(r.get("position_id"), str)})


def emit(lines):
    for ln in lines:
        print(ln, flush=True)


def run(argv=None, *, roots=None, work_allowed=WORK_ALLOWED, manifest_pin=None, sleep_fn=time.sleep, v4_runner=None, table_mode=None, engine_kw=None, head=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--work-root", default=DEFAULT_WORK_ROOT)
    ap.add_argument("--b-tries", type=int, default=B_TRIES)
    ap.add_argument("--b-retry-s", type=int, default=B_RETRY_S)
    a = ap.parse_args(argv)
    head = head or git_head()
    roots = roots or (O.OSB_ROOT,)
    workdir = None
    try:
        try:
            m = O.load_manifest(a.manifest)
            O.guard_paths(m, roots)
            check_ce1_shape(m, manifest_pin)
            check_work_root(a.work_root, m, work_allowed)
            out_root = Path(m["out_root"])
            try:
                pin = (out_root / "manifest.sha256").read_text().strip()
            except OSError:
                raise SetupError("no manifest pin")
            if pin != O.manifest_sha256(m):
                raise SetupError("manifest pin mismatch")
            H.verify_pins(pins=H.pins_for(H.RULE_V4))
            b, _n_rows, b_rows, st = read_b(out_root, roots, sleep_fn, a.b_tries, a.b_retry_s)
            first_ms, last_end, first_key, last_key, count = span_of(m, st)  # the span of the very status the accepted B agreed with
            check_tape_hours(m, first_ms, last_end, roots)
            os.makedirs(a.work_root, exist_ok=True)
            workdir = tempfile.mkdtemp(prefix="run-", dir=a.work_root)
            with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn), contextlib.redirect_stderr(dn):  # the host prints its own progress lines (table checkpoints, counts): only the allowed fields reach the job log
                ledger_rows, entry_rows = (v4_runner or run_v4)(m, last_end, workdir, **({"table_mode": table_mode, "engine_kw": engine_kw} if v4_runner is None else {}))
            a_md5 = C.normalized_md5(ledger_rows, entry_rows)
        except SystemExit:  # floor_tape_paths guards raise SystemExit(text): a recoverable setup error, never the UNEQUAL status 1; the text (paths) is not printed
            emit(["git_head: %s" % head, "SETUP ERROR: tape path refused", "exit_code: %d" % EXIT_SETUP])
            return EXIT_SETUP
        except (SetupError, O.Refused) as e:
            emit(["git_head: %s" % head, "SETUP ERROR: %s" % str(e)[:200], "exit_code: %d" % EXIT_SETUP])
            return EXIT_SETUP
        except (R.HostError, R.HostFatal) as e:  # class name only: HostFatal carries engine exception text
            emit(["git_head: %s" % head, "SETUP ERROR: %s" % type(e).__name__, "exit_code: %d" % EXIT_SETUP])
            return EXIT_SETUP
        except Exception as e:  # noqa: BLE001  (class name only: an exception text could carry a row)
            emit(["git_head: %s" % head, "SETUP ERROR: %s" % type(e).__name__, "exit_code: %d" % EXIT_SETUP])
            return EXIT_SETUP
        eq = C.leg3_equal(a_md5, b)
        code = EXIT_EQUAL if eq else EXIT_UNEQUAL
        emit(["git_head: %s" % head,
              "hours: first=%s last=%s count=%d" % (first_key, last_key, count),
              "seed_from_hour: %s" % m["seed_from_hour"],
              "B: %s" % b,
              "A: %s" % a_md5,
              "result: %s" % ("EQUAL" if eq else "UNEQUAL"),
              "positions: B_distinct_positions=%d A_distinct_positions=%d A_entry_rows=%d" % (distinct_positions(b_rows), distinct_positions(ledger_rows), len(entry_rows)),
              "exit_code: %d" % code])
        return code
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(run())
