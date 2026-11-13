#!/usr/bin/env python3
"""floor_stage1_proof: the md5 decision-equivalence replay proof for the FLOOR lane host "stage 1" (PR #1332, merge aa05923f), QP-RULING-1112-floor-guard-stage1 condition 11
and R5. PAPER replays only. COUNTS, MD5s AND FLAG WORDS ONLY go to stdout (no pool id, no price, no P&L, no row content); a small JSON goes to <out>/<cmd>.json.

  p0  flags off = v1. The host's own `main replay` at the checked-out sha vs the same entry point of the v1 host, over W1, both flags absent. Every decision-log file byte-equal
      (md5 of each whole file, set of file names) and the lane checkpoint md5 equal. W1 must hold ZERO writer status spans (counted from the raw gap rows).
  p1  (required only before `status_span_no_loss` is switched on, a later amendment; Am.7 installs it FALSE) flags on (status_span_no_loss) vs v1 over W2, which must hold real status spans: decisions(v2) = decisions(v1) except v1 refusals turned into entries. Prints the QP
      must-be-0 counts and the number of v2-only entries (>= 1, else the window proves nothing: exit 3).
  p2  restart harness. `main replay` never calls note_rebuild / lane_start / the shadow, so this drives LaneHost in `main run`'s order (table rebuild -> note_rebuild -> lane_start
      -> resume -> run) over tape, with a simulated kill (no finally) at T and a restart at T + D, against the UNGUARDED reference (HostV2 + the frozen rule straight through).
Each subcommand takes --count-only: walks the inputs, checks the path guard and the git objects, prints file counts and hour coverage only (the project's real-layout precount).

THE v1 REFERENCE. `git archive <merge>^1 tools observe` into <out>/trees/v1 (the whole tools/ tree of the parent commit plus observe/, which tools.arena_paper imports; so the pinned files are v1's own too) and `git archive HEAD tools observe`
into <out>/trees/v2; HEAD's floor_lane_host.py must be byte-equal to the working file (clean checkout). Each replay is a SUBPROCESS `python -I this_script _replay --tools-root TREE`
that puts TREE first on sys.path and calls TREE's own `tools.floor_lane_host.main(["replay", ...])`: the pins are verified by TREE's own verify_pins against TREE's own files, no
module is shared between v1 and v2, and nothing is monkeypatched in the real run. (A second worktree would be the same; an archive leaves no git state behind.)

TAPE SOURCE (precount #2549, 2026-11-13): the research-0 hv7 copy keeps only about 240 closed hours and loses one per hour, so a fixed seed hour (the old 2026-11-02T01) is not reachable
and a symlink run could lose inputs mid-run. Outside test mode exactly TWO source roots are accepted: the hv7 copy itself, or a FROZEN HARD-LINK copy
/data2/mal/scratch/floor-stage1-proof/tapecopy-<MMDD>/ made by the manager (same file system, hard links of the hv7 files), which REQUIRES its list file (--tape-list, default
<copy>.list, lines `name size links`). Before any replay the script verifies, counts only: every needed hour is in the list and present with exactly the listed size; for every needed
hour still in hv7 the size is equal and the two names are the same inode (QP's per-hour condition for a copy); the list's sha256 is printed in every output.
SEED: the first closed hour present in the tape source (discovered, not fixed); the host requires seed <= --from - 201 h (SEED_MIN_H); `seed_hour` and `earliest_valid_from` are printed.
With the frozen copy tapecopy-1113 (2026-11-03T01 .. 2026-11-13T00) the earliest valid --from is 2026-11-11T10Z.

WINDOWS (pre-declared; hour-granular, override with --from / --to as YYYY-MM-DDTHH):
  W1 (P0)  [2026-11-12T00Z, 2026-11-12T15Z): the live host entered once on 11-12 at 14:06Z and the day's one writer status span is at 16:04:39Z, so W1 should hold >= 1 entry and 0 status
      spans in [from - 365 min, to). The script verifies both (a status span refuses with exit 1; no entry and no refusal exits 3). The old W1 (11-10T10Z..11-11T00Z) is not reachable from
      the frozen copy's seed.
  W2 (P1, P2)  [2026-11-12T00Z, 2026-11-13T00Z): QP's window (holds the 11-12 status span for P1).
  FALLBACK (pre-declared): if the W2/P2 run is vacuous (exit 3), rerun ONCE with `--from 2026-11-11T10 --to 2026-11-13T00` (the earliest valid start from the frozen copy). The earlier
      fallback from 2026-11-11T00 needs seed 2026-11-02T15 and is NOT reachable. If both are vacuous the manager stops and asks QP: no further window choice. Do not shorten D or the horizon.
  P2  the window of W2 (the reference runs straight through it); T is chosen from the reference's own decisions (a reference Buy inside [T, T+5 min], another position open at T,
      and T + 7 h + horizon inside the window); --t-ms overrides.
P2 SEED-RELAXED RUN (QP-RULING-1113-floor-stage1-p2-vacuous, route B; DEC-035 Am.7 Erratum 2). The frozen-copy P2 runs on 11-11/11-12 were vacuous (exit 3: 2 reference entries, no T). Pre-declared, verbatim from the ruling:
  - P2 only, flag `--seed-relaxed`: the seed must be at least 96 h (not 201 h) before `--from`. P0, P1 and the kill-line check keep 201 h; P0/P1 refuse the flag. With the flag EVERY output line of the
    run carries the word `SEED_RELAXED` and `seed_h_before_from=<n>`. Without the flag behaviour is as before. The relaxation lives only in this script's check_window; `main replay` (which has the host's own
    201 h rule, tools/floor_lane_host.py, pinned and untouched) is not on P2's path: P2 drives LaneHost in `main run`'s order.
  - Run P2 once, at the merged commit of this PR, on [2026-11-08T00Z, 2026-11-10T00Z), tape /data2/mal/scratch/floor-stage1-proof/tapecopy-1113 (list sha256 d0a11d31...), seed 2026-11-03T01 (about 119 h).
  - Only if that exits 3 (vacuous): run once on [2026-11-09T00Z, 2026-11-11T00Z), same seed (about 143 h). If that is vacuous too, STOP and ask QP.
  - MUST NOT: choose or change a window from any P2 output other than the pre-declared vacuity exit (no look at P2-CASE values, MUST0 counts or pass/fail before the window is fixed); try any window not
    listed above; change D, the horizon, the cases or the pass rule; run P2 at an unmerged commit, or on hv7 or tapecopy-roll directly (only a pinned tapecopy-<MMDD> with its list); drop P2 or replace it by the synthetic tests.
  - NOT EXERCISED by a relaxed-seed P2 (disclose in the install record): triggers of pools older than the seed (about 119-143 h at the window start); the AGE_DROP / sweep path for pools past 201 h
    (pinned v1 code in arena_pool_table.py, unchanged by stage 1). The guarded host and the unguarded reference share seed and tape, so every P2 comparison is unaffected in kind.
Nothing here says the rule makes money.

MEMORY / TIME (estimates, not measured): one replay at a time, streaming; the host's own replay peak, <= 8 GB RSS (the pool table of ~8.5 days of seed plus ArenaState); the
2026-11-12 forward A/B replays of ~10 days took 2 h 17 m on 2 cpus, so expect each 14-24 h replay with its 8.5-day seed to take about 0.5-1.5 h. p0 and p1 run two replays each,
sequentially; p2 runs the reference, phase A, and 9 restart cases of (warm-up 365 min + horizon) each.
"""
from __future__ import annotations

import argparse
import re
import copy
import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))
import floor_tape_paths as TP  # noqa: E402

HOUR_MS = 3_600_000
HV7 = "/data2/mal/arena-tip-feed/hv7"
TAPECOPY = "/data2/mal/scratch/floor-nov-funnel/tapecopy"
OUT_ROOT = "/data2/mal/scratch/floor-stage1-proof"
FROZEN_PARENT = "/data2/mal/scratch/floor-stage1-proof"
FROZEN_RE = r"tapecopy-[0-9]{4}"
FALLBACK_W2 = ("2026-11-11T10", "2026-11-13T00")
TAPE_SRC = (HV7,)  # hv7 only (Am.7 precondition (c)); the scratch tapecopy is NOT used. 2026-11-02T01 + 201 h = 2026-11-10T10Z is reachable from hv7 alone
MERGE_HOST_BLOB = "ba7050e1ed3bbf1538a2745f4604110cadb80a7f"  # git blob of tools/floor_lane_host.py at the stage-1 merge aa05923f
MERGE_SHA = "aa05923fc7d2593c4d70c7c80b2f64dee371d700"
SEED_MIN_H = 201
SEED_RELAXED_MIN_H = 96  # P2 only, behind --seed-relaxed (QP-RULING-1113-floor-stage1-p2-vacuous); the host file and its own 201 h rule are untouched
BOT = "l1-floor-v2"
W1 = ("2026-11-12T00", "2026-11-12T15")
W2 = ("2026-11-12T00", "2026-11-13T00")
D_LABELS = ("5min", "2h", "warm-5min", "warm+5min", "7h")
EXIT_FAIL, EXIT_VACUOUS = 1, 3


RELAXED: dict = {}  # set by common() only for `p2 --seed-relaxed`; empty = behaviour as before


class Refuse(SystemExit):
    pass


def refuse(msg: str, code: int = 2):
    print("REFUSED: " + msg + _relaxed_suffix(), file=sys.stderr)
    raise Refuse(code)


def parse_hour(s: str) -> int:
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        refuse("hour must look like 2026-11-10T10 (got %r)" % (s,))


def hour_name(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"))


def _relaxed_suffix() -> str:
    """Empty unless a P2 run with --seed-relaxed has set RELAXED; then the flag word and the seed lead on every output line."""
    if not RELAXED:
        return ""
    return " SEED_RELAXED seed_h_before_from=%s" % RELAXED["seed_h_before_from"]


def emit(tag: str, d: dict) -> None:
    """stdout: counts, md5 and flag words only. Never a row."""
    if RELAXED:
        d = {**d, "seed_h_before_from": RELAXED["seed_h_before_from"]}
        print("%s SEED_RELAXED %s" % (tag, " ".join("%s=%s" % (k, v) for k, v in d.items())), flush=True)
        return
    print("%s %s" % (tag, " ".join("%s=%s" % (k, v) for k, v in d.items())), flush=True)


# ---------------------------------------------------------------------------------------------------------------- guards
def out_roots() -> tuple:
    return (OUT_ROOT,)


def check_out(out: str) -> str:
    """The out dir must be a NEW dir strictly under OUT_ROOT (never overwrite); realpath checked too."""
    ap = os.path.abspath(out)
    if ap.rstrip("/") == OUT_ROOT.rstrip("/") or not (ap.startswith(OUT_ROOT.rstrip("/") + "/") and os.path.realpath(os.path.dirname(ap)).startswith(os.path.realpath(OUT_ROOT))):
        refuse("--out must be a new directory under %s (got %s)" % (OUT_ROOT, ap))
    why = TP.refusal(ap, roots=out_roots())
    if why:
        refuse(why)
    if os.path.lexists(ap):
        refuse("--out exists; never overwrite: %s" % ap)
    return ap


_FROZEN_OK: list = []


def is_frozen_root(d: str) -> bool:
    ap, rp = os.path.abspath(d).rstrip("/"), os.path.realpath(d).rstrip("/")
    return ap == rp and os.path.dirname(ap) == FROZEN_PARENT and re.fullmatch(FROZEN_RE, os.path.basename(ap)) is not None


def tape_roots() -> tuple:
    return tuple(TP.ALLOWED_ROOTS) + tuple(_FROZEN_OK)


def check_srcs(srcs, test_mode: bool = False) -> list:
    """Outside test mode exactly two kinds of root: the hv7 copy, or a frozen hard-link copy /data2/mal/scratch/floor-stage1-proof/tapecopy-<MMDD> (abspath == realpath)."""
    for d in srcs:
        is_hv7 = os.path.abspath(d).rstrip("/") == HV7 and os.path.realpath(d).rstrip("/") == os.path.realpath(HV7).rstrip("/")
        if not test_mode and not (is_hv7 or is_frozen_root(d)):
            refuse("--tape-src must be %s or a frozen copy %s/tapecopy-<MMDD> (got %s)" % (HV7, FROZEN_PARENT, d))
        if is_frozen_root(d) and d not in _FROZEN_OK:
            _FROZEN_OK.append(os.path.abspath(d).rstrip("/"))
        why = TP.refusal(d, roots=tape_roots())
        if why:
            refuse(why)
    return list(srcs)


def discover_seed(srcs) -> int:
    """The first closed hour present in the tape source (file names only)."""
    hrs = []
    for d in srcs:
        for n in os.listdir(d):
            m = re.fullmatch(r"tape-([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2})\.jsonl\.zst", n)
            if m:
                hrs.append(parse_hour(m.group(1)))
    if not hrs:
        refuse("no closed tape hour in the tape source")
    return min(hrs)


def verify_frozen(srcs, seed_ms: int, to_ms: int, list_path: str | None = None, hv7_dir: str | None = None) -> dict:
    """Frozen hard-link copy check (counts only): the list file is required; every needed hour is listed and present with exactly the listed size; for every needed hour still in hv7 the
    size is equal and the two names are the same inode. Refuses on any mismatch. Returns counts and the list's sha256."""
    hv7_dir = hv7_dir or HV7
    out = {"list_sha256": "none", "hours_checked": 0, "hours_same_inode": 0, "hours_no_longer_in_hv7": 0, "size_mismatch_MUST0": 0, "not_same_inode_MUST0": 0, "missing_or_unlisted_MUST0": 0}
    frozen = [d for d in srcs if is_frozen_root(d)]
    if not frozen:
        return out
    if len(frozen) != 1 or len(srcs) != 1:
        refuse("a frozen copy is the only tape source of a run")
    d = frozen[0].rstrip("/")
    lp = list_path or d + ".list"
    if not os.path.isfile(lp):
        refuse("the frozen copy needs its list file: %s" % lp)
    raw = Path(lp).read_bytes()
    out["list_sha256"] = hashlib.sha256(raw).hexdigest()
    listed = {}
    for ln in raw.decode().splitlines():
        f = ln.split()
        if len(f) >= 2 and f[1].isdigit():
            listed[f[0]] = int(f[1])
    for h in hour_list(seed_ms, to_ms):
        name = "tape-%s.jsonl.zst" % h
        out["hours_checked"] += 1
        fp = os.path.join(d, name)
        if name not in listed or not os.path.exists(fp) or os.stat(fp).st_size != listed[name]:
            out["missing_or_unlisted_MUST0"] += 1
            continue
        hp = os.path.join(hv7_dir, name)
        if not os.path.exists(hp):
            out["hours_no_longer_in_hv7"] += 1
            continue
        a_, b_ = os.stat(fp), os.stat(hp)
        if a_.st_size != b_.st_size:
            out["size_mismatch_MUST0"] += 1
        elif (a_.st_ino, a_.st_dev) != (b_.st_ino, b_.st_dev):
            out["not_same_inode_MUST0"] += 1
        else:
            out["hours_same_inode"] += 1
    if out["missing_or_unlisted_MUST0"] or out["size_mismatch_MUST0"] or out["not_same_inode_MUST0"]:
        emit("TAPE-CHECK", out)
        refuse("frozen copy check failed (see TAPE-CHECK counts)")
    return out


def hour_list(seed_ms: int, to_ms: int) -> list:
    return [hour_name(t) for t in range(seed_ms, ((to_ms + HOUR_MS - 1) // HOUR_MS) * HOUR_MS, HOUR_MS)]


def find_closed(srcs, hour: str):
    for d in srcs:
        p = os.path.join(d, "tape-%s.jsonl.zst" % hour)
        if os.path.exists(p):
            return p
    return None


def coverage(srcs, seed_ms: int, to_ms: int) -> dict:
    """File presence only (names are built, nothing is opened)."""
    hrs = hour_list(seed_ms, to_ms)
    missing = [h for h in hrs if find_closed(srcs, h) is None]
    return {"hours_expected": len(hrs), "hours_present": len(hrs) - len(missing), "hours_missing": len(missing), "first_missing_hour": missing[0] if missing else "none"}


def check_window(frm: int, to: int, seed_ms: int, now_ms: int | None = None, min_h: int = SEED_MIN_H) -> None:
    """min_h is SEED_MIN_H (201) everywhere except `p2 --seed-relaxed`, which passes SEED_RELAXED_MIN_H (96)."""
    if to <= frm:
        refuse("empty window")
    if seed_ms > frm - min_h * HOUR_MS:
        earliest = hour_name(seed_ms + min_h * HOUR_MS)
        refuse("--from %s is less than %d h after the seed %s; the earliest valid --from is %s" % (hour_name(frm), min_h, hour_name(seed_ms), earliest))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000) if now_ms is None else now_ms
    if to > now_ms - now_ms % HOUR_MS:
        refuse("--to is the current or a future UTC hour: closed hours only")


def make_tapelink(out: str, srcs, seed_ms: int, to_ms: int, name: str = "tapelink") -> str:
    """Symlinks to the closed hours seed..to only (realpath-checked), in a dir under out. A symlink needs the zstd shim of the .sh wrapper (-f)."""
    d = os.path.join(out, name)
    os.makedirs(d, exist_ok=True)
    for h in hour_list(seed_ms, to_ms):
        src = find_closed(srcs, h)
        if src is None:
            refuse("missing closed hour %s" % h)
        why = TP.refusal(src, roots=tape_roots())
        if why:
            refuse(why)
        os.symlink(src, os.path.join(d, os.path.basename(src)))
    why = TP.refusal(d, roots=tape_roots() + out_roots())
    if why:
        refuse(why)
    return d


# ---------------------------------------------------------------------------------------------------------------- trees
def git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO)] + list(args), check=True, capture_output=True, text=True, timeout=120).stdout.strip()


def export_tree(sha: str, dest: str) -> str:
    """git archive to a FILE first (both exit codes are checked: no pipefail in sh), then tar."""
    os.makedirs(dest, exist_ok=True)
    tarf = dest.rstrip("/") + ".tar"
    with open(tarf, "wb") as f:
        g = subprocess.run(["git", "-C", str(REPO), "archive", sha, "tools", "observe"], stdout=f, stderr=subprocess.PIPE, timeout=300)
    if g.returncode != 0:
        os.remove(tarf)
        refuse("git archive %s failed (shallow clone? fetch the history)" % sha)
    try:
        t = subprocess.run(["tar", "-x", "-C", dest, "-f", tarf], capture_output=True, timeout=300)
    finally:
        os.remove(tarf)
    if t.returncode != 0 or not os.path.exists(os.path.join(dest, "tools", "floor_lane_host.py")):
        refuse("tar of the %s export failed" % sha)
    return dest


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def host_md5(tree: str) -> str:
    return md5_bytes(Path(tree, "tools", "floor_lane_host.py").read_bytes())


def prepare_trees(a, out: str) -> tuple:
    """-> (v1_tree, v2_tree, head_sha). v2 is PINNED to the stage-1 merge (not HEAD): HEAD's host blob and the merge's host blob must both equal --host-blob, and the working file must equal it."""
    head = git("rev-parse", "HEAD")
    if not a.test_mode:
        for what, got in (("HEAD", git("rev-parse", "HEAD:tools/floor_lane_host.py")), ("the merge", git("rev-parse", a.merge_sha + ":tools/floor_lane_host.py"))):
            if got != a.host_blob:
                refuse("%s tools/floor_lane_host.py blob %s is not the stage-1 merge's %s" % (what, got, a.host_blob))
    v1 = a.v1_tree or export_tree(a.merge_sha + "^1", os.path.join(out, "trees", "v1"))
    v2 = a.v2_tree or export_tree(a.merge_sha, os.path.join(out, "trees", "v2"))
    if not a.v2_tree and md5_bytes((REPO / "tools" / "floor_lane_host.py").read_bytes()) != host_md5(v2):
        refuse("the working tools/floor_lane_host.py differs from the merge's: run the proof from a clean checkout")
    return v1, v2, head


# ---------------------------------------------------------------------------------------------------------------- child replay
def child_replay(a) -> int:
    sys.path.insert(0, a.tools_root)
    from tools import arena_rules as R
    from tools import floor_lane_host as H
    from tools.arena_rules_v2 import load_rules_v2
    if not os.path.realpath(H.__file__).startswith(os.path.realpath(a.tools_root)):
        print("child: imported a host outside the tools root", file=sys.stderr)
        return 2
    cfg = json.loads(Path(a.config).read_text())
    os.environ["MAL_ARENA_TEST"] = "1"  # scratch registration only (the same as scripts/research/floor_replay_proof_reg.py)
    R.register_rules(load_rules_v2([H.BOT_ID]), cfg["registry"], H.FROZEN_SHA, int(a.from_ms), now_ms=int(a.from_ms) - 1, verify_sha=False)
    if a.test_mode:  # synthetic tapes only: first_print table, in-memory engine (tests set MAL_ARENA_TEST themselves)
        from tools.arena_rules_testkit import FakeEngine
        orig = H.make_lane_host
        H.make_lane_host = lambda **kw: orig(**{**kw, "table_mode": "first_print", "engine": FakeEngine(Path(kw["root"]))})
    else:
        os.environ.pop("MAL_ARENA_TEST", None)
    argv = ["replay", "--config", a.config, "--from-ms", str(a.from_ms), "--to-ms", str(a.to_ms)]
    if a.seed_from_hour != "none":
        argv += ["--seed-from-hour", a.seed_from_hour]
    return H.main(argv)


def run_replay(tree: str, work: str, tape: str, frm: int, to: int, seed_hour: str, flags: dict, test_mode: bool) -> dict:
    os.makedirs(work, exist_ok=False)
    root = os.path.join(work, "root")
    cfg = {"root": root, "tape_dir": tape, "clock_start_ms": frm, "ledger_root": os.path.join(work, "ledger"), "registry": os.path.join(work, "bots.json")}
    cfg.update({k: True for k, v in flags.items() if v})
    cp = os.path.join(work, "config.json")
    Path(cp).write_text(json.dumps(cfg))
    cmd = [sys.executable, "-I", str(HERE), "_replay", "--tools-root", tree, "--config", cp, "--from-ms", str(frm), "--to-ms", str(to), "--seed-from-hour", seed_hour or "none"]
    if test_mode:
        cmd.append("--test-mode")
    with open(os.path.join(work, "child.log"), "w") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        refuse("replay child failed (exit %d); see %s (not printed)" % (rc, os.path.join(work, "child.log")), 4)
    return {"root": root}


# ---------------------------------------------------------------------------------------------------------------- reading decisions
def decision_dir(root: str) -> Path:
    return Path(root) / "decisions" / BOT


def file_md5s(root: str) -> dict:
    d = decision_dir(root)
    return {f.name: md5_bytes(f.read_bytes()) for f in sorted(d.glob("*.jsonl"))} if d.exists() else {}


def read_rows(root: str) -> list:
    out = []
    for f in sorted(decision_dir(root).glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def ckpt_md5(root: str):
    p = Path(root) / "lane" / "checkpoint.json"
    return md5_bytes(p.read_bytes()) if p.exists() else None


# ---------------------------------------------------------------------------------------------------------------- raw gap rows
def _lines(path: Path):
    if path.name.endswith(".zst"):
        p = subprocess.Popen(["zstd", "-dc", "-f", str(path)], stdout=subprocess.PIPE)
        try:
            for ln in p.stdout:
                yield ln
        finally:
            p.stdout.close()
            p.wait()
    else:
        with open(path, "rb") as f:
            for ln in f:
                yield ln


def scan_gaps(tape_dir: str, lo_ms: int, hi_ms: int) -> dict:
    """Count raw gap rows with t_recv_ms in [lo, hi) from the hour files that can hold them. A WRITER STATUS SPAN is recognised by the host's own predicate
    (floor_lane_host.captured_no_loss_row: type gap, src writer, prog all, kind tip_gap_status_unhealthy, reason status_unhealthy, data_loss true, missed_slots 0, from_slot ==
    to_slot, silent_ms == to_ms - from_ms <= 120 s); `status_kind_any_src` counts the kind without the rest of the predicate. Only integers and durations leave this function."""
    sys.path.insert(0, str(REPO))
    from tools.floor_lane_host import NO_LOSS_KIND, captured_no_loss_row
    files = {}
    for f in Path(tape_dir).iterdir():
        if f.name.startswith("tape-") and (f.name.endswith(".jsonl") or f.name.endswith(".jsonl.zst")):
            h = f.name.split(".")[0][5:]
            try:
                hs = parse_hour(h)
            except Exception:  # noqa: BLE001
                continue
            if hs + HOUR_MS > lo_ms and hs < hi_ms:
                files.setdefault(h, f)
    kinds: dict = {}
    strict, loose, dur = 0, 0, []
    for h in sorted(files):
        for ln in _lines(files[h]):
            if b'"gap"' not in ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            t = r.get("t_recv_ms")
            if r.get("type") != "gap" or not isinstance(t, int) or not (lo_ms <= t < hi_ms):
                continue
            kinds[str(r.get("kind"))] = kinds.get(str(r.get("kind")), 0) + 1
            if r.get("kind") == NO_LOSS_KIND:
                loose += 1
            if captured_no_loss_row(r):
                strict += 1
                dur.append(int(r["to_ms"]) - int(r["from_ms"]))
    return {"n_gap_rows": sum(kinds.values()), "by_kind": dict(sorted(kinds.items())), "status_spans": strict, "status_kind_any_src": loose, "status_durations_ms": sorted(dur)}


# ---------------------------------------------------------------------------------------------------------------- P0
def ckpt_md5_stripped(root: str, drop=("guard", "ended")):
    """md5 of the lane checkpoint without the additive keys (a flags-on run writes `guard`, and `ended` from run()'s finally)."""
    p = Path(root) / "lane" / "checkpoint.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    for k in drop:
        d.pop(k, None)
    return md5_bytes(canon(d).encode())


def kind_counts(rows: list) -> dict:
    return {k: sum(1 for r in rows if r.get("kind") == k) for k in ("entry", "exit", "entry_refused_gap")}


def p0_compare(r1: str, r2: str, r3: str | None = None) -> dict:
    """r1 = v1 (flags absent), r2 = v2 (flags absent), r3 = v2 with BOTH flags on (optional). Every whole decision-log day file md5-equal across all; checkpoint equal for r1/r2 and
    for r3 after removing only `guard` and `ended`."""
    a, b = file_md5s(r1), file_md5s(r2)
    names_equal = sorted(a) == sorted(b)
    n_equal = sum(1 for k in a if b.get(k) == a[k])
    c1, c2 = ckpt_md5(r1), ckpt_md5(r2)
    d = {"files_v1": len(a), "files_v2": len(b), "names_equal": names_equal, "files_md5_equal": n_equal, "files_md5_differ": len(set(a) | set(b)) - n_equal,
         "all_log_md5": md5_bytes(canon(a).encode()), "all_log_md5_v2": md5_bytes(canon(b).encode()), "checkpoint_md5_v1": c1, "checkpoint_md5_v2": c2,
         "checkpoint_equal": c1 is not None and c1 == c2, "rows_v1": len(read_rows(r1)), "rows_v2": len(read_rows(r2))}
    kc = kind_counts(read_rows(r1))
    d.update({"n_entry": kc["entry"], "n_exit": kc["exit"], "n_refusal": kc["entry_refused_gap"]})
    if r3 is not None:
        c = file_md5s(r3)
        d.update({"files_v2on": len(c), "names_equal_on": sorted(c) == sorted(a), "files_md5_differ_on": len(set(a) | set(c)) - sum(1 for k in a if c.get(k) == a[k]),
                  "all_log_md5_v2on": md5_bytes(canon(c).encode()), "rows_v2on": len(read_rows(r3)),
                  "checkpoint_equal_on_minus_guard_ended": ckpt_md5_stripped(r3) is not None and ckpt_md5_stripped(r3) == ckpt_md5_stripped(r1)})
    return d


def p0_pass(c: dict, n_status: int) -> bool:
    ok = c["names_equal"] and c["files_md5_differ"] == 0 and c["files_v1"] > 0 and c["checkpoint_equal"] and n_status == 0
    if "files_v2on" in c:
        ok = ok and c["names_equal_on"] and c["files_md5_differ_on"] == 0 and c["checkpoint_equal_on_minus_guard_ended"]
    return ok


def p0_nonvacuous(c: dict) -> bool:
    return c.get("n_entry", 0) + c.get("n_refusal", 0) >= 1


# ---------------------------------------------------------------------------------------------------------------- P1
def _trig(r: dict):
    m = r.get("meta") or {}
    return (r.get("pool"), m.get("signal_t_recv_ms"))


def _entries(rows):
    return {_trig(r): r for r in rows if r.get("kind") == "entry"}


def _exits(rows):
    d: dict = {}
    for r in rows:
        if r.get("kind") == "exit":
            d.setdefault(r.get("order_id"), []).append((r.get("ms"), r.get("reason"), r.get("decision_slot"), r.get("attempt")))
    return d


def _refs(rows):
    return [r for r in rows if r.get("kind") == "entry_refused_gap"]


def _by_reason(refs):
    d: dict = {}
    for r in refs:
        k = "%s/%s" % (r.get("reason"), (r.get("gap") or {}).get("kind", "none"))
        d[k] = d.get(k, 0) + 1
    return dict(sorted(d.items()))


def p1_compare(rows1: list, rows2: list) -> dict:
    """Pure function of the two decision logs. Keys: entry trigger = (pool, meta.signal_t_recv_ms); refusal trigger = (pool, trigger_ms)."""
    e1, e2 = _entries(rows1), _entries(rows2)
    x1, x2 = _exits(rows1), _exits(rows2)
    ref1, ref2 = _refs(rows1), _refs(rows2)
    t1 = {(r.get("pool"), r.get("trigger_ms")) for r in ref1}
    t2 = {(r.get("pool"), r.get("trigger_ms")) for r in ref2}
    common = sorted(set(e1) & set(e2), key=str)
    v2only = sorted(set(e2) - set(e1), key=str)
    missing = sorted(set(e1) - set(e2), key=str)

    def eproj(r):
        return (r.get("pool"), r.get("ms"), r.get("decision_slot"), r.get("order_id"), canon(r.get("meta")))

    trig_changed = [k for k in common if eproj(e1[k]) != eproj(e2[k])]
    exit_changed = [k for k in common if sorted(x1.get(e1[k]["order_id"], [])) != sorted(x2.get(e2[k]["order_id"], []))]
    v2only_pools_ms = {}
    for k in v2only:
        v2only_pools_ms.setdefault(k[0], []).append(e2[k]["ms"])
    unexplained = [t for t in t1 if t not in t2 and (t[0], t[1]) not in e2 and not any(m <= t[1] for m in v2only_pools_ms.get(t[0], []))]
    at_ref = [k for k in v2only if k in t1]
    c_entries = md5_bytes(canon([eproj(e1[k]) for k in common]).encode())
    c_exits = md5_bytes(canon([sorted(x1.get(e1[k]["order_id"], [])) for k in common]).encode())
    n_ign = sum(1 for r in rows2 if r.get("kind") == "entry_gap_span_ignored")
    d = {"entries_v1": len(e1), "entries_v2": len(e2), "entries_common": len(common), "v2_only_entries": len(v2only), "v2_only_at_v1_refusal_rows": len(at_ref),
         "v2_only_not_at_v1_refusal_MUST0": len(v2only) - len(at_ref), "entries_on_triggers_v1_never_saw_MUST0": len([k for k in v2only if k not in t1]),
         "v1_entries_missing_in_v2_MUST0": len(missing), "triggers_changed_MUST0": len(trig_changed), "exits_changed_MUST0": len(exit_changed),
         "v2_refusals_not_in_v1_MUST0": len(t2 - t1), "v1_refusals_dropped_unexplained_MUST0": len(unexplained),
         "refusal_rows_v1": len(ref1), "refusal_rows_v2": len(ref2), "refusal_triggers_v1": len(t1), "refusal_triggers_v2": len(t2),
         "refusals_v1_by_reason_gapkind": _by_reason(ref1), "refusals_v2_by_reason_gapkind": _by_reason(ref2), "span_ignored_rows_v2": n_ign,
         "common_entry_md5": c_entries, "common_exit_md5": c_exits}
    d["equal_counts_v2only_vs_refusals"] = len(v2only) == len(at_ref)
    d["nonvacuous"] = len(v2only) >= 1
    d["pass"] = all(d[k] == 0 for k in d if k.endswith("MUST0")) and d["equal_counts_v2only_vs_refusals"] and d["nonvacuous"]
    return d


# ---------------------------------------------------------------------------------------------------------------- P2 harness
def _align(t: int, step: int) -> int:
    return t - t % step


def pick_t(ref_rows: list, frm: int, to: int, d_max_ms: int, horizon_ms: int, d_min_ms: int = 300_000):
    """T from the unguarded reference's own decisions: a reference Buy with trigger in (T, T + 5 min], another position open at T, T >= frm + 10 min and T + d_max + horizon <= to."""
    ent = sorted(_entries(ref_rows).items(), key=lambda kv: kv[1]["ms"])
    ex = {}
    for r in ref_rows:
        if r.get("kind") == "exit":
            ex.setdefault(r["order_id"], r["ms"])
    for k, e in ent:
        b = int(k[1])
        T = _align(b - 120_000, 60_000)
        if T < frm + 600_000 or T + d_max_ms + horizon_ms > to:
            continue
        if any(o["ms"] <= T and ex.get(o["order_id"], 2 ** 62) > T for kk, o in ent if kk != k):
            return T
    return None


def p2_case_metrics(name, ref_rows, T, D, host, rows, rec, eng, expect_fallback, restart_now, horizon_ms: int = 0, step_ms: int = 1000, hold_ms: int | None = None) -> dict:
    ref_e = _entries(ref_rows)
    ref_ex = {}
    for r in ref_rows:
        if r.get("kind") == "exit":
            ref_ex.setdefault(r["order_id"], r["ms"])
    rs = [r for r in rows if r.get("kind") == "lane_restart_shadow"]
    rs = rs[-1] if rs else {}
    shadow_pools = set(rs.get("shadow_pools") or [])
    complete = bool(rs.get("shadow_complete"))
    in_dt = {k[0] for k in ref_e if T <= int(k[1]) <= T + D}
    n_in_dt = sum(1 for k in ref_e if T <= int(k[1]) <= T + D)
    # QP B2: a reference position open in [T, T + D + 10 s] is covered only if, after catch_up, the pool's block lasts to the position's exit (to min(exit, entry + HOLD_MS + one tick)), or the
    # lane holds an OPEN position on that pool at the restart. No "ever entered" term; the existence of a block is not enough.
    if hold_ms is None:
        from tools.arena_rule_l1 import HOLD_MS as hold_ms
    blocked_until = rec["blocked_pools"] if isinstance(rec["blocked_pools"], dict) else {p: 2 ** 62 for p in rec["blocked_pools"]}
    lane_open = set(rec.get("lane_open_pools") or ())
    n_open, uncovered = 0, 0
    for k, e in ref_e.items():
        ex_ms = ref_ex.get(e["order_id"], 2 ** 62)
        if e["ms"] <= T + D + 10_000 and ex_ms >= T:
            n_open += 1
            need = min(ex_ms, int(e["ms"]) + hold_ms + step_ms)  # QP B3: to the exit; a position never outlives its decision + HOLD_MS + one tick
            if not (k[0] in lane_open or int((blocked_until.get(k[0]) or 0)) >= need):
                uncovered += 1
    post = [r for r in rows if r.get("kind") == "entry" and r["ms"] >= restart_now]
    post_not_ref = [r for r in post if _trig(r) not in ref_e]
    stamped_in_down = [r for r in rows if r.get("kind") == "entry" and T + 1_000 < r["ms"] < restart_now]
    shadow_rows = (rec["seq_post"] - rec["seq_pre"]) - (1 if rs else 0)
    d = {"case": name, "prev_end": rs.get("prev_end", "none"), "fallback": rs.get("fallback") or "none", "shadow_complete": complete, "n_pools_blocked_by_shadow": len(shadow_pools),
         "n_ref_buys_in_downtime": n_in_dt, "n_ref_pools_bought_in_downtime_missed_MUST0": len(in_dt - shadow_pools) if complete else 0,
         "blocked_equals_ref_buys": shadow_pools == in_dt, "n_ref_positions_open_in_downtime": n_open, "n_ref_positions_open_in_downtime_uncovered_MUST0": uncovered if complete else 0,
         "n_post_restart_entries": len(post), "n_post_restart_entries_not_in_reference_MUST0": len(post_not_ref),
         "n_dispatched_during_shadow_MUST0": rec["buys_post"] - rec["buys_pre"] + len(stamped_in_down), "n_rows_written_by_shadow_MUST0": shadow_rows,
         "fallback_as_expected": (rs.get("fallback") or "none") == expect_fallback}
    d["post_entries_zero_when_fallback_MUST0"] = 0 if complete else len(post)
    d["pass"] = (d["fallback_as_expected"] and d["shadow_complete"] == (expect_fallback == "none") and d["n_ref_buys_in_downtime"] >= 1 and
                 all(d[k] == 0 for k in d if k.endswith("MUST0")))
    return d


DISK_FREE_MIN_FRAC = 0.255  # CLAUDE.md: keep / at >= 25.5 % free; the same margin is applied to the proof's file system


def tree_bytes(path: str) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def disk_guard(src: str, copies: int, where: str, usage=shutil.disk_usage) -> None:
    """P2 copies phase A's root once per case: refuse unless the copies fit and free space stays >= 25.5 % of the file system afterwards."""
    need = tree_bytes(src) * copies
    u = usage(where)
    if u.free - need < DISK_FREE_MIN_FRAC * u.total:
        refuse("disk: %d copies of phase A's root need %d bytes; free %d of %d would drop below %.1f %%" % (copies, need, u.free, u.total, DISK_FREE_MIN_FRAC * 100))


def register_scratch(reg: str, frm: int, test_mode: bool) -> None:
    from tools import arena_rules as R
    from tools import floor_lane_host as H
    from tools.arena_rules_v2 import load_rules_v2
    os.environ["MAL_ARENA_TEST"] = "1"
    try:
        R.register_rules(load_rules_v2([BOT]), reg, H.FROZEN_SHA, frm, now_ms=frm - 1, verify_sha=False)
    finally:
        if not test_mode:
            os.environ.pop("MAL_ARENA_TEST", None)


def reference_rows(work: str, reg: str, tape: str, frm: int, to: int, step: int, tm, do_seed: bool) -> list:
    """The UNGUARDED reference: HostV2 + the frozen rule straight through [frm, to) (no GapGuardRule). Returns its decision rows; the host is freed before return."""
    import gc
    from tools import arena_rules as R
    from tools import floor_lane_host as H
    from tools.arena_rules_testkit import FakeEngine
    from tools.arena_rules_v2 import HostV2, load_rules_v2, rebuild_table
    from tools.arena_state import ArenaState, WARM_MIN_DEFAULT
    warm = WARM_MIN_DEFAULT
    rroot = os.path.join(work, "ref")
    rules = load_rules_v2([BOT])
    if tm:
        rules[0].table_mode = tm
    state = ArenaState(tape, start_ms=frm - warm * 60_000, warm_min=warm)
    cfg = R.HostConfig(root=rroot, registry=reg, frozen_sha=H.FROZEN_SHA, clock_start_ms=frm, live=False)
    ref = HostV2(cfg, rules, state, FakeEngine(Path(rroot)))
    if do_seed:
        rebuild_table(ref.table, tape, frm - warm * 60_000)
    ref.resume()
    ref.catch_up(frm)
    for t in range(frm, to, step):
        ref.step(t)
    rows = read_rows(rroot)
    del ref, state, rules
    gc.collect()
    return rows


def run_p2(a, out: str, tape: str, seed_ms) -> dict:
    sys.path.insert(0, str(REPO))
    from tools import arena_rules as R
    from tools import floor_lane_host as H
    from tools.arena_rules_testkit import FakeEngine
    from tools.arena_rules_v2 import HostV2, load_rules_v2, rebuild_table
    from tools.arena_state import ArenaState, WARM_MIN_DEFAULT
    frm, to, step = a.from_ms, a.to_ms, a.step_ms
    warm = WARM_MIN_DEFAULT
    tm = "first_print" if a.test_mode else None
    head_sha = git("rev-parse", "HEAD") if not a.test_mode else "a" * 40
    work = os.path.join(out, "p2")
    os.makedirs(work)
    reg = os.path.join(work, "bots.json")
    os.environ["MAL_ARENA_TEST"] = "1"
    try:
        R.register_rules(load_rules_v2([BOT]), reg, H.FROZEN_SHA, frm, now_ms=frm - 1, verify_sha=False)
    finally:
        if not a.test_mode:
            os.environ.pop("MAL_ARENA_TEST", None)

    def seed(host, upto):
        return rebuild_table(host.table, tape, upto) if seed_ms is not None or a.test_mode else 0

    # -- the unguarded reference: HostV2 + the frozen rule, straight through
    ref_rows = reference_rows(work, reg, tape, frm, to, step, tm, bool(seed_ms is not None or a.test_mode))
    n_ref = len(_entries(ref_rows))
    d_ms = {"5min": 300_000, "2h": 7_200_000, "warm-5min": warm * 60_000 - 300_000, "warm+5min": warm * 60_000 + 300_000, "7h": 25_200_000}
    d_ms = {k: v for k, v in d_ms.items()}
    horizon = a.horizon_s * 1000
    T = a.t_ms if a.t_ms else pick_t(ref_rows, frm, to, max(d_ms.values()), horizon)
    res = {"ref_entries": n_ref, "t_found": T is not None, "cases": []}
    if T is None:
        res["pass"] = False
        res["why"] = "no T with a reference Buy in (T, T+5min] and another position open at T inside the window"
        return res
    T = _align(T, step)
    # -- phase A: LaneHost live (fake clock) from the window start, killed at T (no finally, no end mark)
    aroot = os.path.join(work, "A")
    clock = [frm]
    engA = FakeEngine(Path(aroot))

    def lane(root, eng, clk, now0, **flags):
        h = H.make_lane_host(root=root, ledger_root=os.path.join(root, "ledger"), registry=reg, tape_dir=tape, clock_start_ms=frm, live=True, binding=H.lane_binding(H.verify_pins(), hb={"git_sha": head_sha, "dirty": False}, guard={"status_span_no_loss": bool(flags.get("status_span_no_loss")), "cheap_restart": bool(flags.get("cheap_restart"))}), start_ms=now0 - warm * 60_000,
                             table_mode=tm, engine=eng, now_fn=lambda: clk[0], warm_min=warm, **flags)
        if step > 5_000:
            h.cfg.stale_row_ms = 10 ** 9
        return h

    A = lane(aroot, engA, clock, frm, cheap_restart=True)
    seed(A, frm - warm * 60_000)
    A.resume()
    A.catch_up(frm)
    for t in range(frm, T, step):
        clock[0] = t + 100
        A.step(t)
    del A
    gc.collect()
    disk_guard(aroot, 10, work)
    cases = [("a_" + k, v, "none" if v <= warm * 60_000 - 120_000 else "downtime_exceeds_warmup", None) for k, v in d_ms.items()]
    cases += [("c_exception", 300_000, "unclean_exit", lambda ck: ck.update(ended="exception")), ("d_in_doubt", 300_000, "unclean_exit", lambda ck: ck.update(ended="in_doubt")),
              ("e_unknown_v1_checkpoint", 300_000, "unknown_prev_end", lambda ck: (ck.pop("guard", None), ck.pop("ended", None))),
              ("e_unknown_status_flag_only", 300_000, "unknown_prev_end", lambda ck: (ck.update(guard={"status_span_no_loss": True, "cheap_restart": False}), ck.pop("ended", None)))]
    for name, D, expect, edit in cases:
        croot = os.path.join(work, name)
        shutil.copytree(aroot, croot)
        ckp = Path(croot) / "lane" / "checkpoint.json"
        if edit:
            ck = json.loads(ckp.read_text())
            edit(ck)
            ckp.write_text(canon(ck))
        eng = FakeEngine(Path(croot))
        eng.positions = copy.deepcopy(engA.positions)
        now = _align(T + D, step)
        clk = [now]
        B = lane(croot, eng, clk, now, cheap_restart=True)
        ws = now - warm * 60_000
        t_start = time.monotonic()
        n = rebuild_table(B.table, tape, ws)  # exactly main run's rebuild with a checkpoint and no --since-hour
        B.note_rebuild(ws, n)
        B.lane_start(now)
        B.resume()
        rec = {"seq_pre": 0, "seq_post": 0, "buys_pre": 0, "buys_post": 0, "blocked_pools": set()}
        orig = B.catch_up

        def cu(t_from, orig=orig, B=B, eng=eng, rec=rec):
            rec["seq_pre"] = B.log.head(BOT)[1]
            rec["lane_open_pools"] = {getattr(x, "pool", None) for x in eng.positions.values()}
            rec["buys_pre"] = sum(1 for c in eng.calls if c[0] == "buy")
            orig(t_from)
            rec["seq_post"] = B.log.head(BOT)[1]
            rec["buys_post"] = sum(1 for c in eng.calls if c[0] == "buy")
            rec["blocked_pools"] = {p_: int(b_["until_ms"]) for p_, b_ in B.blocked.items()}
            rec["start_s"] = round(time.monotonic() - t_start, 3)

        B.catch_up = cu
        start = max(frm, now)
        B.run(start - start % 1000, start + horizon, sleep=lambda s, clk=clk: clk.__setitem__(0, clk[0] + max(20, int(s * 1000))))
        m = p2_case_metrics(name, ref_rows, T, D, B, read_rows(croot), rec, eng, expect, now, 0, step)
        m["D_s"] = D // 1000
        m["start_duration_s_INFO"] = rec.get("start_s")
        m["table_rebuild_rows_INFO"] = n
        res["cases"].append(m)
        if name == "e_unknown_v1_checkpoint":
            del B
            gc.collect()
            res["f"] = run_f(None, eng, croot, lane, tape, rebuild_table, now + horizon, step, warm, frm)
        shutil.rmtree(os.path.join(croot, "ledger"), ignore_errors=True)
        if name != "e_unknown_v1_checkpoint":
            del B, eng
            gc.collect()
    # fail-closed: a decision log without its lane checkpoint refuses to start (main run without --since-hour and without a checkpoint)
    mroot = os.path.join(work, "nockpt")
    shutil.copytree(aroot, mroot)
    os.remove(os.path.join(mroot, "lane", "checkpoint.json"))
    M = lane(mroot, FakeEngine(Path(mroot)), [T], T, cheap_restart=True)
    try:
        M.lane_start(T + 300_000)
        refused = False
    except R.HostError:
        refused = True
    res["missing_checkpoint_refuses"] = refused
    res["t_ms"] = T
    res["pass"] = all(c["pass"] for c in res["cases"]) and refused and res.get("f", {}).get("pass", False)
    return res


def run_f(B1, eng, croot, lane, tape, rebuild_table, end1, step, warm, frm) -> dict:
    """QP case (f), a continuation of the v1-checkpoint case: restart 1 (unknown_prev_end, refuse-all) ended cleanly at end1; restart 2 inside 24 h still refuses every pool because
    the old host_down span is unflagged; restart 3, after that span aged out, is cheap. `refuses` = the host's own judge() for a pool with no block of its own, 5 s after the start."""
    from tools import floor_lane_host as FH

    def restart(now, name):
        clk = [now]
        H_ = lane(croot, eng, clk, now, cheap_restart=True)
        ws = now - warm * 60_000
        n = rebuild_table(H_.table, tape, ws)
        H_.note_rebuild(ws, n)
        info = H_.lane_start(now)
        H_.resume()
        H_.begin_live(now)
        H_.catch_up(now - now % step)
        j = H_.judge(now + 5_000, "probe-pool-without-a-block")
        rs = [r for r in read_rows(croot) if r.get("kind") == "lane_restart_shadow"][-1]
        d = {"case": name, "prev_end": rs.get("prev_end"), "fallback": rs.get("fallback") or "none", "shadow_complete": bool(rs.get("shadow_complete")), "old_unflagged_span_refuses_every_pool": j is not None and any(g_.get("kind") == "host_down" for g_ in j[1])}
        return H_, clk, d

    now2 = _align(end1 + 600_000, step)
    H2, clk2, d2 = restart(now2, "f2_restart_inside_24h_of_unflagged_span")
    unfl = [g for g in H2.lane_gaps if g.get("kind") == "host_down" and not g.get("shadow_complete")]
    aged = max((g["to_ms"] for g in unfl), default=now2) + FH.BLOCK_MS + 2 * step
    t = now2
    while t < aged:  # a clean run, then a kill (no finally): the checkpoint says `signal`
        clk2[0] = t + 100
        H2.step(t)
        t += step
    del H2
    gc.collect()
    now3 = _align(t + 300_000, step)
    H3, clk3, d3 = restart(now3, "f3_restart_after_the_span_aged_out")
    d3["n_unflagged_spans_in_checkpoint"] = len(unfl)
    d2["pass"] = d2["old_unflagged_span_refuses_every_pool"] is True
    d3["pass"] = d3["old_unflagged_span_refuses_every_pool"] is False and d3["shadow_complete"] is True and d3["prev_end"] == "signal"
    return {"f2": d2, "f3": d3, "pass": d2["pass"] and d3["pass"]}


# ---------------------------------------------------------------------------------------------------------------- main
def common(a, default_w, p):
    frm = parse_hour(a.from_) if a.from_ else parse_hour(default_w[0])
    to = parse_hour(a.to) if a.to else parse_hour(default_w[1])
    a.from_ms, a.to_ms = frm, to
    srcs = check_srcs(a.tape_src or list(TAPE_SRC), a.test_mode)
    if a.test_mode and os.environ.get("MAL_ARENA_TEST") != "1":
        refuse("--test-mode needs MAL_ARENA_TEST=1")
    if a.seed_from_hour == "none":
        seed_ms = None
        if not a.test_mode:
            refuse("--seed-from-hour none is for synthetic tests only")
    elif a.seed_from_hour == "auto":
        seed_ms = discover_seed(srcs)
        a.seed_from_hour = hour_name(seed_ms)
    else:
        seed_ms = parse_hour(a.seed_from_hour)
    min_h = SEED_MIN_H
    RELAXED.clear()
    if getattr(a, "seed_relaxed", False):
        if p != "p2":
            refuse("--seed-relaxed is for p2 only (P0, P1 and the kill-line check keep the %d h seed rule)" % SEED_MIN_H)
        if seed_ms is None:
            refuse("--seed-relaxed needs a seed hour")
        min_h = SEED_RELAXED_MIN_H
        RELAXED["seed_h_before_from"] = (frm - seed_ms) // HOUR_MS
    a.tape_info = {"test_mode": bool(a.test_mode), "seed_hour": a.seed_from_hour, "earliest_valid_from": hour_name(seed_ms + min_h * HOUR_MS) if seed_ms is not None else "none"}
    if seed_ms is not None:
        a.tape_info.update(verify_frozen(srcs, seed_ms, to, a.tape_list))
        check_window(frm, to, seed_ms, now_ms=a.now_ms, min_h=min_h)
    else:
        a.tape_info["list_sha256"] = "none"
    return seed_ms, frm, to, srcs


def precount(a, cmd, seed_ms, frm, to, srcs) -> int:
    cov = coverage(srcs, seed_ms if seed_ms is not None else frm, to)
    head = git("rev-parse", "HEAD")
    try:
        parent = git("rev-parse", a.merge_sha + "^1")
        blob1 = md5_bytes(subprocess.run(["git", "-C", str(REPO), "show", parent + ":tools/floor_lane_host.py"], capture_output=True, check=True).stdout)
        git_ok = True
    except Exception:  # noqa: BLE001
        blob1, git_ok = "none", False
    emit("COUNT-ONLY", {"cmd": cmd, "window_hours": (to - frm) // HOUR_MS, "seed_h_before_from": ((frm - seed_ms) // HOUR_MS) if seed_ms is not None else "none",
                        **a.tape_info, **cov, "tape_roots_ok": True, "out_ok": True,
                        "v1_parent_resolvable": git_ok, "v1_host_md5": blob1, "v2_host_md5": md5_bytes((REPO / "tools" / "floor_lane_host.py").read_bytes()), "head_is_set": bool(head)})
    return 0 if cov["hours_missing"] == 0 and git_ok else 2


def cmd_p0(a) -> int:
    seed_ms, frm, to, srcs = common(a, W1, "p0")
    if a.count_only:
        return precount(a, "p0", seed_ms, frm, to, srcs)
    out = check_out(a.out)
    os.makedirs(out)
    tape = make_tapelink(out, srcs, seed_ms if seed_ms is not None else frm, to) if not a.test_mode else srcs[0]
    sc = scan_gaps(tape, frm - 365 * 60_000, to)
    emit("P0-WINDOW", {"status_spans": sc["status_spans"], "status_kind_any_src": sc["status_kind_any_src"], "n_gap_rows": sc["n_gap_rows"], "by_kind": sc["by_kind"]})
    if sc["status_spans"] != 0:
        refuse("W1 holds %d writer status spans: P0 would not be strict" % sc["status_spans"], EXIT_FAIL)
    v1, v2, head = prepare_trees(a, out)
    sh = a.seed_from_hour
    r1 = run_replay(v1, os.path.join(out, "v1"), tape, frm, to, sh, {}, a.test_mode)["root"]
    r2 = run_replay(v2, os.path.join(out, "v2"), tape, frm, to, sh, {}, a.test_mode)["root"]
    r3 = run_replay(v2, os.path.join(out, "v2on"), tape, frm, to, sh, {"cheap_restart": True, "status_span_no_loss": True}, a.test_mode)["root"]
    c = p0_compare(r1, r2, r3)
    ok = p0_pass(c, sc["status_spans"])
    c.update(**a.tape_info, status_spans_in_w1=sc["status_spans"], host_md5_v1=host_md5(v1), host_md5_v2=host_md5(v2), pass_=ok, nonvacuous=p0_nonvacuous(c))
    emit("P0", c)
    Path(out, "p0.json").write_text(json.dumps(c, sort_keys=True))
    if not p0_nonvacuous(c):
        print("P0 VACUOUS: no entry and no refusal in W1; equality proves nothing", file=sys.stderr)
        return EXIT_VACUOUS
    print("P0 %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else EXIT_FAIL


def cmd_p1(a) -> int:
    seed_ms, frm, to, srcs = common(a, W2, "p1")
    if a.count_only:
        return precount(a, "p1", seed_ms, frm, to, srcs)
    out = check_out(a.out)
    os.makedirs(out)
    tape = make_tapelink(out, srcs, seed_ms if seed_ms is not None else frm, to) if not a.test_mode else srcs[0]
    sc = scan_gaps(tape, frm - 365 * 60_000, to)
    dur = sc["status_durations_ms"]
    emit("P1-WINDOW", {"status_spans": sc["status_spans"], "status_kind_any_src": sc["status_kind_any_src"], "durations_ms": ",".join(map(str, dur[:30])) or "none", "n_gap_rows": sc["n_gap_rows"],
                       "by_kind": sc["by_kind"]})
    if sc["status_spans"] == 0:
        refuse("W2 holds no writer status span: v2 cannot differ from v1 there (vacuous)", EXIT_VACUOUS)
    v1, v2, head = prepare_trees(a, out)
    sh = a.seed_from_hour
    r1 = run_replay(v1, os.path.join(out, "v1"), tape, frm, to, sh, {}, a.test_mode)["root"]
    r2 = run_replay(v2, os.path.join(out, "v2"), tape, frm, to, sh, {"status_span_no_loss": True}, a.test_mode)["root"]
    d = p1_compare(read_rows(r1), read_rows(r2))
    d["status_spans_in_window"] = sc["status_spans"]
    d.update(a.tape_info)
    emit("P1", d)
    Path(out, "p1.json").write_text(json.dumps(d, sort_keys=True))
    if not d["nonvacuous"]:
        print("P1 VACUOUS: no v2-only entry; the window proves nothing", file=sys.stderr)
        return EXIT_VACUOUS
    print("P1 %s" % ("PASS" if d["pass"] else "FAIL"))
    return 0 if d["pass"] else EXIT_FAIL


def cmd_p2(a) -> int:
    seed_ms, frm, to, srcs = common(a, W2, "p2")
    if a.count_only:
        return precount(a, "p2", seed_ms, frm, to, srcs)
    out = check_out(a.out)
    os.makedirs(out)
    tape = make_tapelink(out, srcs, seed_ms if seed_ms is not None else frm, to) if not a.test_mode else srcs[0]
    prepare_trees(a, out) if not a.test_mode else None
    res = run_p2(a, out, tape, seed_ms)
    if not res["t_found"]:
        print("P2 VACUOUS: %s%s" % (res["why"], _relaxed_suffix()), file=sys.stderr)
        Path(out, "p2.json").write_text(json.dumps(res, sort_keys=True))
        return EXIT_VACUOUS
    for c in res["cases"]:
        c.update(a.tape_info)
        emit("P2-CASE", c)
    for k in ("f2", "f3"):
        emit("P2-F", res["f"][k])
    emit("P2-CHECK", {"missing_checkpoint_refuses": res["missing_checkpoint_refuses"]})
    Path(out, "p2.json").write_text(json.dumps(res, sort_keys=True))
    print("P2 %s%s" % ("PASS" if res["pass"] else "FAIL", _relaxed_suffix()))
    return 0 if res["pass"] else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] + "  NOTE: p1 is required only before status_span_no_loss is switched on (a later amendment); the Am.7 install needs p0 and p2.", formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("p0", "p1", "p2", "_replay"):
        p = sub.add_parser(name)
        if name == "_replay":
            p.add_argument("--tools-root", required=True)
            p.add_argument("--config", required=True)
            p.add_argument("--from-ms", type=int, required=True)
            p.add_argument("--to-ms", type=int, required=True)
            p.add_argument("--seed-from-hour", default="none")
            p.add_argument("--test-mode", action="store_true")
            continue
        p.add_argument("--out", required=True, help="a NEW directory under %s" % OUT_ROOT)
        p.add_argument("--from", dest="from_", default=None, help="window start hour YYYY-MM-DDTHH")
        p.add_argument("--to", default=None, help="window end hour (exclusive)")
        p.add_argument("--seed-from-hour", default="auto", help="auto = the first closed hour present in the tape source")
        p.add_argument("--tape-list", default=None, help="list file of a frozen copy (default <copy>.list)")
        p.add_argument("--tape-src", action="append", default=None)
        p.add_argument("--merge-sha", default=MERGE_SHA)
        p.add_argument("--host-blob", default=MERGE_HOST_BLOB)
        p.add_argument("--v1-tree", default=None, help="a pre-exported v1 tree (tools/ of the parent commit)")
        p.add_argument("--v2-tree", default=None)
        p.add_argument("--count-only", action="store_true")
        p.add_argument("--test-mode", action="store_true", help="synthetic tapes only (needs MAL_ARENA_TEST=1)")
        p.add_argument("--now-ms", type=int, default=None)
        p.add_argument("--seed-relaxed", action="store_true", help="P2 ONLY (QP-RULING-1113): accept a seed at least %d h (not %d h) before --from; every output line then carries SEED_RELAXED and seed_h_before_from" % (SEED_RELAXED_MIN_H, SEED_MIN_H))
        if name == "p2":
            p.add_argument("--t-ms", type=int, default=None)
            p.add_argument("--horizon-s", type=int, default=14_400)
            p.add_argument("--step-ms", type=int, default=1000)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "_replay":
        return child_replay(a)
    return {"p0": cmd_p0, "p1": cmd_p1, "p2": cmd_p2}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
