#!/usr/bin/env python3
"""s1t_tip_p0_count: the OUTCOME-BLIND slip P0 counter of the s1t book (`h5v1q50-boostfloor-s1t`) on the research-0 derived tip copy.
DEC-031 Amendment 24 item 5 ("The count is S*' with item 6's pricing-off switch, as the pinned tip-source P0 counter (Amendment 23 item 9)"); QP-RULING-1106-s1t-slip section 4.
A NEW pinned file. `tools/hv7_tip_p0_count.py` is HV7's rule U2 and is NOT valid for s1t; it is only the template for the refusals, H0, closed days and counts-only style.

WHAT IT COUNTS. It runs S*''s own, frozen engine (tag `q50-succ-tip-1` = commit afa4971d, `tools/h5_shadow_tip.py` + `tools/h5_shadow.py`) in harness mode, exactly as `tools/h5_shadow_tip_equiv.py`
does at that commit: `TipRowSource` over the derived copy, `TipDriver`, `h5.Engine` with q_star_sol 50.0 and `fresh_start_ms` = H0, and `h5_shadow_tip.arm_smoke` (Item 6's pricing-off switch: the
Look-2 seal on EVERY pool, so no outcome, ladder, strip or min_q is ever computed; a trigger record is written flagged `h5_look2_sealed`). The state is built from H0 (the first hour of the copy's continuous
history); only the three days are COUNTED (a trigger counts on the UTC day of its trigger print, `t_recv_ms`); the hours from H0 to day 1 are state only.
  * Structure = a POOL on which the Q50 rule fires, once per pool per day (`pools`); `records` counts the trigger records (variants pv and fv, as S*' defines them: a pool can write both), and
    `records_pv` / `records_fv` split them. `p0_per_day` = the mean of the three `pools` counts, UNROUNDED: that value goes to `s1t_register --p0-per-day`.
  * Class: there is no side feed offline, and the live smoke's synthetic class cannot be known here, so the engine runs with `StaticClassifier(False)`: every fired structure is written as a trigger
    record, none is withheld as synthetic/unclassified. The P0 is therefore a count of rule structures BEFORE the class exclusion (a declared upper-side choice, the P0's purpose is the rule's frequency).
  * Nothing else: no price, outcome, exit, fill or P&L exists in this pass. No pool, mint, signature, price or per-pool row is ever printed; the output has a FIXED key set (tested).

WHICH ENGINE (the choice, explained in the PR). The counter does not import S*' from this checkout. `--sstar-src` names a git worktree and the counter refuses (exit 2) unless that worktree's HEAD is exactly
afa4971d9dd8b0bca2e73a758c22f4539f675d63, `tools/h5_shadow.py` is blob 7f859b12... and `tools/h5_shadow_tip.py` is blob 05c5489a... (judged on the DISK BYTES, git-blob hashed, not on the index), and the tree
is clean (tracked and untracked). The engine then runs in a child interpreter (`python -I -B`, so no cwd/PYTHONPATH/user site and no bytecode written into the worktree) whose sys.path puts the worktree
first; the child checks that every `tools.*` module it loaded came from that worktree. main's `tools/h5_shadow.py` is a different blob (f23cf492), which is why a main import would be the wrong bytes.

Refusals (each a `Refused` with a reason, exit 2; an unexpected error is exit 3, class name only):
  * `tape_dir`: the research-0 derived copy (`TIP_FEED_ROOT`) or, tests only, a directory under the system temp dir. Anything else, a `.writer` component, `sealed`, `tip-tape-archive`: refused.
  * `--h0` (YYYY-MM-DDTHH): not before 2026-10-16T01 (the D2/D3 seal) and before day 1.
  * days: exactly 3 contiguous ascending FULL CLOSED UTC days, each >= 2026-11-06; a non-empty `--notebook-ref`.
  * HOURS (QP-RULING-1106 section 4, the difference from hv7): "a day with incomplete derived-copy history is a declared undercount and is still counted". A missing hour file is never silent and never a
    refusal: it is listed (hour names only) and the day is in `undercount_days` (a day is an undercount if any of its 24 hour files, or the hour just before it, is missing). A copy with NO file for
    the three days at all is refused (`no_hours_in_days`): there is nothing to count.
  * `--sstar-src`: identity above. The counter writes NO file anywhere (it prints one JSON line).
  * If the engine emits any outcome-type record (`outcome`, `outcome_bx10`, `strip`) or a trigger without `h5_look2_sealed`: exit 3, no counts printed.
  * The tip input must be healthy at start (`push_problem`, fail closed as S*' does) and stay so: a problem mid-read is exit 3, never a quiet undercount.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SSTAR_COMMIT = "afa4971d9dd8b0bca2e73a758c22f4539f675d63"       # tag q50-succ-tip-1 (S*')
SSTAR_BLOBS = {"tools/h5_shadow.py": "7f859b12deef87573a7314e49e125b5121eaa645", "tools/h5_shadow_tip.py": "05c5489a786dac4eb0696a74279a0f789ddb1deb"}
TIP_FEED_ROOT = "/data2/mal/arena-tip-feed/hv7"
TIP_WRITER_DIR = ".writer"
FIRST_DAY = "2026-11-06"                                         # Am.24 item 5: the slip P0's days are 11-06, 11-07, 11-08 (all after Q50's counted window)
H0_FLOOR = "2026-10-16T01"                                       # D2/D3: no hour before it is ever read
H0_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}")
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
HOUR_FILE = re.compile(r"tape-(\d{4}-\d{2}-\d{2})T(\d{2})\.jsonl(?:\.zst)?")
N_DAYS = 3
DAY_MS, HOUR_MS = 86_400_000, 3_600_000
Q_STAR_SOL = 50.0
BLOCKED_TYPES = ("outcome", "outcome_bx10", "strip")             # == h5_shadow_tip.SMOKE_BLOCKED_TYPES; re-checked in the child
KEEP_COUNTERS = ("problems", "gap_rows", "gap_rows_flagging", "rows_before_start", "rows_bad", "reader_loss_events", "reader_loss_bad_lines", "reader_loss_zst_errors",
                 "reader_loss_apply_errors", "reader_errors", "polls_unhealthy", "decode_errors")       # loss/structural counters only; no per-kind row count (pool_create, trade, complete)
TEST_TMP_ROOTS = (tempfile.gettempdir().rstrip("/") + "/",)
PURPOSE = ("s1t slip P0 (DEC-031 Amendment 24 item 5): per-day count of Q50-rule structures by S*' (q50-succ-tip-1, afa4971d) under the pricing-off switch; "
           "p0_per_day goes to s1t_register --p0-per-day. Structure counts only; no outcome exists. No book has cleared the promotion gate.")


class Refused(Exception):
    """A refusal with a short reason code (exit 2)."""


def day_ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------------------------------------------ refusals
def check_tape_dir(tape_dir: str | os.PathLike) -> Path:
    p = os.fspath(tape_dir)
    if not p or "\x00" in p or not os.path.isabs(p):
        raise Refused("tape_dir_not_absolute")
    real = os.path.realpath(p)
    if real.split(os.sep).count(TIP_WRITER_DIR):
        raise Refused("tip_writer_state")
    if real == os.path.realpath(TIP_FEED_ROOT):
        return Path(real)
    if any(real.startswith(os.path.realpath(r.rstrip("/")) + os.sep) for r in TEST_TMP_ROOTS) and not any(t in real.lower() for t in ("sealed", "tip-tape-archive")):
        return Path(real)
    raise Refused("tape_dir_not_allowed")


def parse_days(arg: str, *, now_ms: int) -> list[str]:
    days = [d.strip() for d in arg.split(",") if d.strip()]
    if len(days) != N_DAYS or len(set(days)) != N_DAYS:
        raise Refused("need_exactly_3_distinct_days")
    for d in days:
        if not DAY_RE.fullmatch(d):
            raise Refused("day_format")
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise Refused("day_format") from None
        if d < FIRST_DAY:
            raise Refused("day_before_2026_11_06")
        if day_ms(d) + DAY_MS > now_ms:
            raise Refused("day_not_closed")
    dts = [datetime.strptime(d, "%Y-%m-%d") for d in days]
    if dts != sorted(dts) or any((b - a) != timedelta(days=1) for a, b in zip(dts, dts[1:])):
        raise Refused("days_not_contiguous_ascending")
    return days


def parse_h0(arg: Any, days: list[str]) -> tuple[str, int]:
    if not isinstance(arg, str) or not H0_RE.fullmatch(arg):
        raise Refused("h0_format")
    try:
        t = datetime.strptime(arg, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    except ValueError:
        raise Refused("h0_format") from None
    if arg < H0_FLOOR:
        raise Refused("h0_before_2026_10_16T01")
    ms = int(t.timestamp() * 1000)
    if ms >= day_ms(days[0]):
        raise Refused("h0_not_before_first_day")
    return arg, ms


def blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()  # noqa: S324 - git object id, not security


def _git(src: str, *args: str) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C"}
    r = subprocess.run(["git", "-C", src, *args], capture_output=True, text=True, env=env, timeout=60)
    if r.returncode != 0:
        raise Refused("sstar_src_not_a_git_worktree")
    return r.stdout


def check_sstar_src(path: str | os.PathLike) -> dict[str, str]:
    """S*' identity: HEAD == afa4971d, the two engine files are the pinned blobs ON DISK, tracked and untracked tree clean. Returns the pins it saw."""
    p = os.fspath(path)
    if not p or "\x00" in p or not os.path.isabs(p):
        raise Refused("sstar_src_not_absolute")
    real = os.path.realpath(p)
    if not os.path.isdir(real):
        raise Refused("sstar_src_not_a_dir")
    if os.path.realpath(_git(real, "rev-parse", "--show-toplevel").strip()) != real:
        raise Refused("sstar_src_not_a_worktree_root")
    head = _git(real, "rev-parse", "HEAD").strip()
    if head != SSTAR_COMMIT:
        raise Refused("sstar_head_mismatch")
    for rel, want in SSTAR_BLOBS.items():
        f = Path(real) / rel
        if not f.is_file() or f.is_symlink() or blob_sha1(f.read_bytes()) != want:
            raise Refused("sstar_blob_mismatch")
    if _git(real, "status", "--porcelain", "--untracked-files=all").strip():
        raise Refused("sstar_tree_dirty")
    return {"commit": head, **{rel.rsplit("/", 1)[-1]: v for rel, v in SSTAR_BLOBS.items()}}


def hour_coverage(tape: Path, h0_key: str, days: list[str]) -> dict[str, Any]:
    """Names only (a directory listing); no file is opened. A missing hour is DECLARED, never a refusal and never silent (QP-RULING-1106 section 4)."""
    present = set()
    for p in sorted(tape.iterdir()):
        m = HOUR_FILE.fullmatch(p.name)
        if m:
            present.add(f"{m.group(1)}T{m.group(2)}")
    fmt = "%Y-%m-%dT%H"
    h0 = datetime.strptime(h0_key, fmt).replace(tzinfo=timezone.utc)
    per_day: dict[str, dict[str, Any]] = {}
    for d in days:
        t0 = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        keys = [(t0 + timedelta(hours=h)).strftime(fmt) for h in range(24)]
        miss = [k for k in keys if k not in present]
        prev = (t0 - timedelta(hours=1)).strftime(fmt)
        prev_missing = prev not in present and prev >= h0_key
        per_day[d] = {"hours_present": 24 - len(miss), "hours_expected": 24, "hours_missing": miss, "hour_before_missing": prev_missing, "undercount": bool(miss) or prev_missing}
    if all(v["hours_present"] == 0 for v in per_day.values()):
        raise Refused("no_hours_in_days")
    t, end, hist_missing = h0, datetime.strptime(days[0], "%Y-%m-%d").replace(tzinfo=timezone.utc), 0
    while t < end:
        hist_missing += t.strftime(fmt) not in present
        t += timedelta(hours=1)
    return {"per_day": per_day, "history_hours_missing_before_day_1": hist_missing}


# ------------------------------------------------------------------------------------------------------------------------------------ the engine (child)
def _child(cfg: dict[str, Any]) -> dict[str, Any]:
    """Runs in `python -I -B` with the S*' worktree first on sys.path. Imports nothing from the counter's own checkout."""
    src = os.path.realpath(cfg["src"])
    sys.path.insert(0, src)
    import collections

    from tools import h5_shadow as h5
    from tools import h5_shadow_tip as tip

    for mod in (h5, tip):
        if not os.path.realpath(mod.__file__).startswith(src + os.sep):
            raise RuntimeError("engine_not_from_sstar_src")
    if tuple(tip.SMOKE_BLOCKED_TYPES) != BLOCKED_TYPES:
        raise RuntimeError("blocked_types_changed")
    days, start, end = cfg["days"], cfg["days_start_ms"], cfg["days_end_ms"]
    counts = {d: {"pools": set(), "records": 0, "records_pv": 0, "records_fv": 0} for d in days}

    def sink(rec: dict) -> None:
        typ = rec.get("type")
        if typ in BLOCKED_TYPES:
            raise RuntimeError("outcome_record_emitted")
        if typ != "trigger":
            return
        if rec.get("h5_look2_sealed") is not True:
            raise RuntimeError("trigger_not_sealed")
        t = rec.get("t_recv_ms")
        if not isinstance(t, int) or isinstance(t, bool) or not (start <= t < end):
            return                                  # history (state only) or malformed: not counted
        c = counts[_utc_day(t)]
        c["pools"].add(rec.get("pool"))
        c["records"] += 1
        if rec.get("variant") in ("pv", "fv"):
            c["records_" + rec["variant"]] += 1

    h0_ms = cfg["h0_ms"]
    eng = h5.Engine(sink, boost_mode="auto", classifier=h5.StaticClassifier(False, "p0_class_assumed"), bx10_enabled=False, fresh_start_ms=h0_ms, q_star_sol=Q_STAR_SOL,
                    suppress_outcome=None, pick_oracle=None, seal_start_ms=None, h5_look2_start_ms=h5.SUCC_END_MS, h5_look2_observed=False)
    guard = tip.arm_smoke(eng)                       # item 6's pricing-off switch: the Look-2 seal on every pool
    roots = cfg.get("roots")
    src_obj = tip.TipRowSource(cfg["tape"], start_ms=h0_ms, roots=roots, problem_fn=(lambda: None) if cfg.get("test_no_status") else None, cache_ms=0 if roots else None)
    try:
        src_obj.start()                              # refuses on any problem (fail closed)
    except tip.TipInputUnhealthy:
        raise RuntimeError("tip_input_unhealthy_start") from None
    drv = tip.TipDriver(eng, src_obj, wall_offset_ms=2500)
    while True:
        n = drv.step(tip.ROWS_PER_STEP)
        if guard["blocked"]:
            raise RuntimeError("outcome_record_emitted")
        if src_obj.problem:
            raise RuntimeError("tip_input_problem")
        if n == 0:
            break
    merged = collections.Counter(drv.counters)
    merged.update(src_obj.counters())
    merged["decode_errors"] += eng.counters["decode_errors"]
    return {"per_day": {d: {"pools": len(c["pools"]), "records": c["records"], "records_pv": c["records_pv"], "records_fv": c["records_fv"]} for d, c in counts.items()},
            "input_counters": {k: int(merged[k]) for k in KEEP_COUNTERS if k in merged}, "blocked": guard["blocked"]}


def _child_main() -> int:
    try:
        cfg = json.loads(sys.stdin.read())
        out = _child(cfg)
    except Exception as e:  # noqa: BLE001 - class name and a short code only, never row content
        print(json.dumps({"error": type(e).__name__, "code": str(e)[:40] if isinstance(e, RuntimeError) else ""}))
        return 1
    print(json.dumps(out, sort_keys=True))
    return 0


def run_engine(src: str, tape: Path, days: list[str], h0_ms: int, *, roots: list[str] | None) -> dict[str, Any]:
    cfg = {"src": src, "tape": str(tape), "days": days, "days_start_ms": day_ms(days[0]), "days_end_ms": day_ms(days[-1]) + DAY_MS, "h0_ms": h0_ms, "roots": roots,
           "test_no_status": roots is not None}
    r = subprocess.run([sys.executable, "-I", "-B", os.path.abspath(__file__), "--child"], input=json.dumps(cfg), capture_output=True, text=True, timeout=6 * 3600,
                       cwd=src, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "MAL_ARENA_TEST": "1"})
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError("child_no_output") from None
    if r.returncode != 0 or "error" in out:
        code = out.get("code") if isinstance(out, dict) else ""
        if code == "tip_input_unhealthy_start":
            raise Refused("tip_input_unhealthy")
        raise RuntimeError(code or "child_failed")
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ the count
def run_p0(days_arg: str, notebook_ref: str, h0: str, sstar_src: str, *, tape_dir: str | os.PathLike = TIP_FEED_ROOT, now_ms: int | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if not (isinstance(notebook_ref, str) and notebook_ref.strip()):
        raise Refused("notebook_ref_required")
    tape = check_tape_dir(tape_dir)
    days = parse_days(days_arg, now_ms=now)
    h0_key, h0_ms = parse_h0(h0, days)
    pins = check_sstar_src(sstar_src)
    cov = hour_coverage(tape, h0_key, days)
    test_dir = os.path.realpath(tape) != os.path.realpath(TIP_FEED_ROOT)
    res = run_engine(os.path.realpath(sstar_src), tape, days, h0_ms, roots=[str(tape.parent)] if test_dir else None)
    per_day = {d: {**res["per_day"][d], **cov["per_day"][d]} for d in days}
    pools = [per_day[d]["pools"] for d in days]
    recs = [per_day[d]["records"] for d in days]
    return {"s1t_tip_p0": "counts", "purpose": PURPOSE, "sstar": pins, "pricing_off": True, "outcome_free": True, "class": "assumed_non_synthetic_none_excluded", "first_day": days[0], "last_day": days[-1],
            "h0": h0_key, "notebook_ref": notebook_ref, "unit": "pools with >= 1 Q50-rule trigger record per UTC day of the trigger print", "per_day": per_day, "p0_per_day": sum(pools) / len(pools),
            "records_per_day_mean": sum(recs) / len(recs), "undercount_days": [d for d in days if per_day[d]["undercount"]], "history_hours_missing_before_day_1": cov["history_hours_missing_before_day_1"],
            "input_counters": res["input_counters"]}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--child"]:
        return _child_main()
    ap = argparse.ArgumentParser(description="s1t slip P0: per-day Q50-rule structure counts by S*' (pricing-off) on 3 closed days of the derived copy. Counts only; no outcome exists.")
    ap.add_argument("--days", required=True, help="three contiguous closed UTC days >= 2026-11-06, fixed in a notebook entry first")
    ap.add_argument("--notebook-ref", required=True)
    ap.add_argument("--h0", required=True, help="H0, the first hour of the derived copy's continuous history, YYYY-MM-DDTHH (UTC); the state is built from it")
    ap.add_argument("--sstar-src", required=True, help="a clean git worktree at afa4971d (tag q50-succ-tip-1) whose engine blobs are the pinned S*' bytes")
    a = ap.parse_args(argv)
    try:
        res = run_p0(a.days, a.notebook_ref, a.h0, a.sstar_src)
    except Refused as e:
        print(json.dumps({"s1t_tip_p0": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name (and a short code for our own RuntimeErrors) only
        print(json.dumps({"s1t_tip_p0": "error", "class": type(e).__name__, "code": str(e)[:40] if isinstance(e, RuntimeError) else ""}), file=sys.stderr)
        return 3
    print(json.dumps(res, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
