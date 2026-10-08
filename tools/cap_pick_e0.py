"""EXP-022 E0 harness: md5 decision-equivalence of the live-gate replay, the runner replay and the scorer (EXP/EXP-022-cap-pick-part1-prereg.md 2.1).

Exploration pools only. One UTC day of one exploration view, never forward-walk, walk-2 or any sealed block. Offline, no network, no key.

    python -m tools.cap_pick_e0 run   --view explore-0814 --day 2026-08-17 --out DIR
    python -m tools.cap_pick_e0 check DIR/e0.json [--worktree PATH]

THE THREE LISTS (all for the same day; both sides boot at 00:00Z of that day)
  A  the runner's own replay path, `forward_paper.replay_rows`, with the frozen gated EXP-012 book, preloading creator history with
     `exp012_boot=(staged creates, 00:00Z)` (the hook that makes `replay_rows` boot as `main` does). Decisions are read from A's `exp012_gate` JsonlLog.
  B  `cap_pick_gate_replay.replay_view` for the day (what `cap_pick_gate_replay replay --view V --from-day D --to-day D` calls), daily restart on.
     B's file is written in the CLI's own layout (meta line, then `kind: decision` and `kind: dead` rows) and is the file the scorer reads.
  C  the mints counted as attempts by `tools/cap_pick_score.py --book picks --picks <B file>`, against B's `pick` mints inside U, the attempt mints of the
     same scorer run with `--book all` (same flags, same --picks, only --book differs). The scorer is run with `--only-day D` and the narrowest source
     flags that cover the view (its roots, from `cap_pick_gate_replay.BLOCKS`), so it reads D's trade hours plus its own two look-ahead hours.
  Canonical decision list: one line per mint, sorted by mint, `mint<TAB>decision<TAB>mig_ms<TAB>repr(score)`, an empty field when the score is null.
  md5(A) must equal md5(B); md5(C list) must equal md5(B pick mints in U). Both canonical files are built by `canon_lines`.

LABEL MAPPING (A's gate rows carry `entered` and `reason`, not a label; B's records carry `decision`). Applied to A only, and 1:1:
  entered true                       -> pick
  entered false, reason below_threshold -> below
  entered false, any other reason    -> that reason (no_features, no_bond_history, gate_error, ...); a null reason -> unknown
  This is the mapping `Replayer._on_signal` applies to the very same `_exp012_pass` row, so B's label is a function of the same two fields. No label of B
  collides with a different A reason. `mig_ms` and `score` have the same name and value on both sides (the gate row's `mig_ms` and `score`).

WHAT IS SHARED, NOT UNDER TEST (disclosed; they are inputs, not the runner)
  * Creates are built by `cap_pick_gate_replay.create_signal_from_row` on both sides (the live observe path's create handling is what B mirrors).
    First create row of a mint wins across the day, in file order, as B does. Only hours with a trades file are read, as B does.
  * Trade rows: the day's rows in file order, `t_recv_ms` imputed as block_time*1000 where null (B's rule), A drops rows past the end of the day
    (`replay_rows` does; counted as `rows_past_day_end`).
  * A is fed only trade rows of mints that have a create row in the day at or before the row's hour (counted in `rows_no_create`). `replay_rows` has no dead-mint
    set, so it would buffer every other row in `engine.early` for ever and never decide on it; B drops the same rows. This is a memory bound, not a decision
    filter, and it is the one place A's input is narrower than "the day's trade rows".
  * The staged creates files are the ones B stages (`cap_pick_gate_replay.stage_creates`, same inputs). The hook preloads without a `tape_dir`
    (B does not pass one either; `main` does, when a tape directory exists).
  * `dead` rows (a mint created before the 00:00Z restart, which the runner never decides) are dropped from B's canonical list. A has no create for them and
    no decision. Their count is recorded (`n_dead_B`).
  * The frozen gated book: the BookSpec of `cap_pick_gate_replay.build_engine` (model md5 checked at load, threshold 0.8030766588450794).

PINS (EXP-022 2.1 item 4)
  Git blob shas at HEAD of the four pinned modules, the md5 of ARTIFACTS/exp012/FROZEN.md5 (must be a01f05dfb1e622f78b2bba55d174be09), and a check that the
  files Python actually imported hash to those blobs. `run` refuses unless the git tree is clean (untracked files count) and HEAD is contained in a remote
  branch `origin/*` known locally (no fetch is done: no network). `check` repeats the pin comparison against HEAD, and the working copy, of any worktree.

Exit codes: 0 every check holds; 1 a check failed (A != B, C != B picks in U, ...); 2 refused or a step could not run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from tools import cap_pick_gate_replay as cp

REPO = Path(__file__).resolve().parent.parent
SCHEMA = "cap_pick_e0_v1"
DAY_MS = 86_400_000
PINNED_MODULES = ("tools/forward_exp012_gate.py", "tools/forward_paper.py", "tools/exploration_entry_model.py", "tools/cap_pick_gate_replay.py")
FROZEN_MD5_PATH = "ARTIFACTS/exp012/FROZEN.md5"
FROZEN_MD5_EXPECTED = "a01f05dfb1e622f78b2bba55d174be09"
SCORER_PATH = "tools/cap_pick_score.py"
SCORER_MODULE = "tools.cap_pick_score"
# scorer source flag per view; "append" flags take one value per root, the others one directory
SCORER_SOURCE: dict[str, tuple[str, str]] = {
    "explore-0814": ("--p2-view-dir", "append"),
    "exp011-0909": ("--p4-view-dir", "append"),
    "fresh-0903": ("--p3-root", "parent"),
    "fast-pool-0918": ("--p1-fast-dir", "single"),
    "oracle-insample-0922": ("--p1-oracle-insample-dir", "single"),
}
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class E0Error(Exception):
    """A refusal or a step that could not run (exit 2)."""


# ---- git ----------------------------------------------------------------------------------------------
def _git(repo: Path, *args: str, binary: bool = False) -> Any:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if r.returncode != 0:
        raise E0Error(f"git {' '.join(args)} failed in {repo}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout if binary else r.stdout.decode("utf-8")


def repo_state(repo: Path) -> dict[str, Any]:
    """Refuse unless the tree is clean (untracked files count) and HEAD is on a local `origin/*` ref. Returns HEAD and those refs."""
    head = _git(repo, "rev-parse", "HEAD").strip()
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=normal").strip()
    if dirty:
        shown = "; ".join(dirty.splitlines()[:5])
        raise E0Error(f"git tree is not clean in {repo} ({len(dirty.splitlines())} entries: {shown})")
    refs = [x.strip() for x in _git(repo, "branch", "-r", "--contains", head).splitlines()]
    on_origin = sorted(x for x in refs if x.startswith("origin/") and "->" not in x)
    if not on_origin:
        raise E0Error(f"HEAD {head} of {repo} is not on any origin/* branch known locally (push it; E0 does not fetch)")
    return {"head": head, "origin_branches": on_origin}


def head_blob(repo: Path, rel: str) -> str:
    return _git(repo, "rev-parse", f"HEAD:{rel}").strip()


def file_blob(repo: Path, path: Path) -> str:
    """The git blob sha of a file on disk (`git hash-object`)."""
    return _git(repo, "hash-object", str(path)).strip()


def head_md5(repo: Path, rel: str) -> str:
    return hashlib.md5(_git(repo, "cat-file", "blob", f"HEAD:{rel}", binary=True)).hexdigest()


def collect_pins(repo: Path) -> dict[str, Any]:
    """Blob shas at HEAD of the pinned modules, the FROZEN.md5 md5 and its check."""
    blobs = {rel: head_blob(repo, rel) for rel in PINNED_MODULES}
    md5 = head_md5(repo, FROZEN_MD5_PATH)
    return {"blobs": blobs, "frozen_md5": {"path": FROZEN_MD5_PATH, "md5": md5, "expected": FROZEN_MD5_EXPECTED, "ok": md5 == FROZEN_MD5_EXPECTED}}


def imported_blobs(repo: Path) -> dict[str, dict[str, str]]:
    """The pinned modules as Python imported them: the file and its git blob sha. Importing is what makes these the modules that run."""
    out: dict[str, dict[str, str]] = {}
    for rel in PINNED_MODULES:
        mod = importlib.import_module(rel[:-3].replace("/", "."))
        f = Path(getattr(mod, "__file__", "") or "").resolve()
        out[rel] = {"file": str(f), "blob": file_blob(repo, f)}
    return out


def verify_pins(e0: dict[str, Any], worktree: Path) -> dict[str, Any]:
    """Recorded pins against HEAD and the working copy of `worktree`. A pin holds if both equal the recorded blob; FROZEN.md5 must hash to the expected md5."""
    mods: dict[str, Any] = {}
    for rel, want in e0["blobs"].items():
        got_head = head_blob(worktree, rel)
        p = worktree / rel
        got_disk = file_blob(worktree, p) if p.is_file() else None
        mods[rel] = {"recorded": want, "head": got_head, "worktree_file": got_disk, "ok": got_head == want and got_disk == want}
    md5_head = head_md5(worktree, FROZEN_MD5_PATH)
    fp = worktree / FROZEN_MD5_PATH
    md5_disk = hashlib.md5(fp.read_bytes()).hexdigest() if fp.is_file() else None
    want_md5 = e0["frozen_md5"]["md5"]
    frozen = {"recorded": want_md5, "expected": FROZEN_MD5_EXPECTED, "head": md5_head, "worktree_file": md5_disk,
              "ok": want_md5 == FROZEN_MD5_EXPECTED and md5_head == want_md5 and md5_disk == want_md5}
    return {"worktree": str(worktree), "head": _git(worktree, "rev-parse", "HEAD").strip(), "recorded_commit": e0.get("commit"),
            "e0_ok": bool(e0.get("ok")), "modules": mods, "frozen_md5": frozen,
            "ok": all(m["ok"] for m in mods.values()) and frozen["ok"] and bool(e0.get("ok"))}


# ---- canonical lists ----------------------------------------------------------------------------------
def label_from_gate_row(row: dict[str, Any]) -> str:
    """A's decision label from a runner gate row (`_exp012_pass`'s row). The mapping `Replayer._on_signal` applies to the same row."""
    if row.get("entered"):
        return "pick"
    reason = row.get("reason")
    if reason == "below_threshold":
        return "below"
    return reason or "unknown"


def canon_lines(rows: Iterable[tuple[str, str, int, float | None]], side: str = "") -> list[str]:
    """`mint<TAB>decision<TAB>mig_ms<TAB>repr(score)` (empty field if the score is None), sorted by mint. A mint twice is an error, not a dedupe."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for mint, decision, mig_ms, score in rows:
        if mint in seen:
            raise E0Error(f"side {side or '?'} has two decisions for mint {mint}")
        seen.add(mint)
        out.append((mint, f"{mint}\t{decision}\t{mig_ms}\t{'' if score is None else repr(score)}"))
    out.sort(key=lambda t: t[0])
    return [line for _m, line in out]


def canon_text(lines: Sequence[str]) -> str:
    return "".join(line + "\n" for line in lines)


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def canon_from_gate_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    return canon_lines(((r["mint"], label_from_gate_row(r), r["mig_ms"], r["score"]) for r in rows), "A")


def canon_from_b_records(records: Iterable[dict[str, Any]]) -> tuple[list[str], int]:
    """B's `kind: decision` rows. `dead` rows are dropped (returned as a count); the meta line and anything else is not a decision."""
    keep, dead = [], 0
    for r in records:
        k = r.get("kind")
        if k == "decision":
            keep.append((r["mint"], r["decision"], r["mig_ms"], r["score"]))
        elif k == "dead":
            dead += 1
    return canon_lines(keep, "B"), dead


def diff_rows(a: Sequence[str], b: Sequence[str]) -> list[tuple[str, str, str]]:
    """(mint, A line, B line) for every mint whose lines differ or that one side lacks. Tabs inside a line are shown as `|`."""
    da = {ln.split("\t", 1)[0]: ln for ln in a}
    db = {ln.split("\t", 1)[0]: ln for ln in b}
    out = []
    for m in sorted(set(da) | set(db)):
        if da.get(m) != db.get(m):
            out.append((m, da.get(m, "<absent>").replace("\t", "|"), db.get(m, "<absent>").replace("\t", "|")))
    return out


# ---- side B -------------------------------------------------------------------------------------------
def write_b_file(path: Path, recs: Sequence[dict[str, Any]], meta: dict[str, Any]) -> None:
    """The layout of `cap_pick_gate_replay.cmd_replay`'s output: a meta line, then one JSON object per record."""
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"schema": cp.SCHEMA, "kind": "meta", **meta}) + "\n")
        for r in recs:
            fh.write(json.dumps(r) + "\n")


def read_b_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] = {}
    recs: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("schema") != cp.SCHEMA:
            raise E0Error(f"{path}: not a {cp.SCHEMA} file")
        if r.get("kind") == "meta":
            meta = r
        else:
            recs.append(r)
    return meta, recs


# ---- side A -------------------------------------------------------------------------------------------
def a_creates(files: dict[str, dict[str, Path]], hours: Sequence[str], roots: Sequence[str]) -> tuple[list[Any], dict[str, str], dict[str, int]]:
    """CreateSignals of the day's hours (those with a trades file), first row of a mint wins, as B's `feed_hour`. Also mint -> hour of its create."""
    creates: list[Any] = []
    first_hour: dict[str, str] = {}
    imputed = [0]
    n_rows = 0
    for hour in hours:
        f = files[hour].get("creates")
        if f is None:
            continue
        for row in cp.iter_json_rows(f, roots):
            n_rows += 1
            c = cp.create_signal_from_row(row, imputed=imputed)
            if c is None or c.mint in first_hour:
                continue
            first_hour[c.mint] = hour
            creates.append(c)
    return creates, first_hour, {"create_rows": n_rows, "creates": len(creates), "create_rows_t_recv_imputed": imputed[0]}


def a_trade_rows(files: dict[str, dict[str, Path]], hours: Sequence[str], roots: Sequence[str], first_hour: dict[str, str], end_ms: int,
                 stats: dict[str, int]) -> Iterator[dict[str, Any]]:
    """The day's trade rows in file order for `replay_rows`, with B's `t_recv_ms` imputation. See the module note on the create filter."""
    for hour in hours:
        for line in cp.iter_lines(files[hour]["trades"], roots):
            mint = cp.quick_mint(line)
            if mint is None:
                continue
            fh = first_hour.get(mint)
            if fh is None or fh > hour:
                stats["rows_no_create"] += 1
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            t = row.get("t_recv_ms")
            if not isinstance(t, int) or isinstance(t, bool):
                block = row.get("block_time", row.get("event_ts"))
                if isinstance(block, bool) or not isinstance(block, int):
                    continue
                row["t_recv_ms"] = block * 1000
                stats["rows_t_recv_imputed"] += 1
            if row["t_recv_ms"] > end_ms:
                stats["rows_past_day_end"] += 1
            stats["rows_fed"] += 1
            yield row


def run_side_a(block: cp.Block, files: dict[str, dict[str, Path]], roots: Sequence[str], day: str, specs: Sequence[Any], out_dir: Path,
               *, boot: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A: the runner's `replay_rows` on the day. Returns the gate rows read back from its `exp012_gate` JsonlLog and a stats dict."""
    from tools.forward_paper import JsonlLog, replay_rows

    boot_ms = cp._calendar_ms(day)
    end_ms = boot_ms + DAY_MS - 1
    hours = sorted(h for h in files if h[:10] == day and "trades" in files[h])
    stats: dict[str, Any] = {"hours": len(hours), "rows_fed": 0, "rows_no_create": 0, "rows_past_day_end": 0, "rows_t_recv_imputed": 0, "tape_end_ms": end_ms}
    creates, first_hour, cstats = a_creates(files, hours, roots)
    stats.update(cstats)
    gate_path = out_dir / "A.gate-rows.jsonl"
    gate_path.unlink(missing_ok=True)
    glog = JsonlLog(gate_path)
    with tempfile.TemporaryDirectory(prefix="cap_pick_e0_stage_") as td:
        stage = Path(td)
        stats["staged_files"] = cp.stage_creates(files, boot_ms, stage, roots)
        try:
            engine = replay_rows(creates, a_trade_rows(files, hours, roots, first_hour, end_ms, stats), specs, tape_end_ms=end_ms,
                                 kill_file=out_dir / "KILL_e0_unused", logs={"exp012_gate": glog},
                                 exp012_boot=(stage, boot_ms) if boot else None)
        finally:
            glog.close()
    stats["history_rows"] = engine.exp012.preload_stats.get("rows", 0) if engine.exp012 is not None and engine.exp012.preload_stats else 0
    rows = [json.loads(ln) for ln in gate_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    stats["engine_gate_rows"] = len(engine.exp012_rows)
    stats["dead_prints_dropped"] = engine.dead_prints_dropped
    stats["early_prints_buffered"] = sum(len(v) for v in engine.early.values())
    return rows, stats


# ---- side C: the scorer -------------------------------------------------------------------------------
def scorer_source_flags(view: str, roots: Sequence[str]) -> list[str]:
    flag, mode = SCORER_SOURCE[view]
    if mode == "append":
        return [x for r in roots for x in (flag, str(r))]
    if mode == "single":
        if len(roots) != 1:
            raise E0Error(f"view {view}: scorer flag {flag} takes one directory, the view has {len(roots)} roots")
        return [flag, str(roots[0])]
    parents = {str(Path(r).parent) for r in roots}
    if len(parents) != 1:
        raise E0Error(f"view {view}: roots do not share one parent for {flag}")
    return [flag, parents.pop()]


def scorer_cmd(view: str, roots: Sequence[str], day: str, picks: Path, out_dir: Path, book: str, extra: Sequence[str]) -> list[str]:
    return [sys.executable, "-m", SCORER_MODULE, *scorer_source_flags(view, roots), "--only-day", day, "--book", book, "--picks", str(picks),
            "--out-dir", str(out_dir), *extra]


def read_rows_mints(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [r["mint"] for r in csv.DictReader(fh)]


def subprocess_scorer(scorer_repo: Path, view: str, roots: Sequence[str], day: str, picks: Path, out_dir: Path, book: str, extra: Sequence[str],
                      log_path: Path) -> tuple[list[str], list[str]]:
    """Run the scorer in `scorer_repo`; return (attempt mints in rows.csv order, the command)."""
    cmd = scorer_cmd(view, roots, day, picks, out_dir, book, extra)
    with log_path.open("w", encoding="utf-8") as lf:
        r = subprocess.run(cmd, cwd=str(scorer_repo), stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise E0Error(f"scorer --book {book} exited {r.returncode} (see {log_path})")
    rows = out_dir / "rows.csv"
    if not rows.is_file():
        raise E0Error(f"scorer --book {book} wrote no rows.csv in {out_dir}")
    return read_rows_mints(rows), cmd


Scorer = Callable[[str, Path, Path], "tuple[list[str], list[str]]"]  # (book, picks file, out dir) -> (attempt mints, command)


# ---- the run ------------------------------------------------------------------------------------------
def view_hashes(block: cp.Block) -> dict[str, str]:
    out: dict[str, str] = {}
    for root in block.roots:
        p = Path(root) / "VIEW.sha256"
        if not p.is_file():
            raise E0Error(f"{p}: no VIEW.sha256 for root {root}; E0 pins a view by it")
        out[str(root)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def default_engine_factory() -> Any:
    return cp.build_engine()


def run_e0(view: str, day: str, out: Path, *, block: cp.Block | None = None, engine_factory: Callable[[], Any] | None = None,
           scorer: Scorer | None = None, repo: Path = REPO, scorer_repo: Path | None = None, scorer_extra: Sequence[str] = (),
           skip_c: bool = False, log: Any = sys.stderr) -> dict[str, Any]:
    """The whole E0 for one view-day. Writes A.canon, B.canon, B.jsonl, C.list, Bpicks_in_U.list, the md5 files, diff.tsv (if A != B) and e0.json."""
    t_start = time.monotonic()
    if not _DAY_RE.match(day):
        raise E0Error(f"--day must be YYYY-MM-DD, got {day!r}")
    block = block or cp.BLOCKS[view]
    engine_factory = engine_factory or default_engine_factory
    state = repo_state(repo)
    scorer_state = None
    if scorer is None and not skip_c and scorer_repo is not None and Path(scorer_repo).resolve() != Path(repo).resolve():
        scorer_state = repo_state(scorer_repo)
    pins = collect_pins(repo)
    imported = imported_blobs(repo)
    view_sha = view_hashes(block)
    cp.check_days([day])
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise E0Error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    roots = list(block.roots)
    wall: dict[str, float] = {}

    # B
    t = time.monotonic()
    recs, bmeta = cp.replay_view(block, [day], engine=engine_factory(), roots=roots, log=log)
    wall["B"] = round(time.monotonic() - t, 1)
    bmeta = {**bmeta, "wall_s": wall["B"]}
    b_path = out / "B.jsonl"
    write_b_file(b_path, recs, bmeta)
    b_lines, n_dead = canon_from_b_records(recs)
    del recs

    # A
    t = time.monotonic()
    files = cp.hour_files(block, roots)
    specs = [run.spec for run in engine_factory().books]
    a_rows, astats = run_side_a(block, files, roots, day, specs, out)
    wall["A"] = round(time.monotonic() - t, 1)
    a_lines = canon_from_gate_rows(a_rows)

    a_text, b_text = canon_text(a_lines), canon_text(b_lines)
    _write(out / "A.canon", a_text)
    _write(out / "B.canon", b_text)
    md5_a, md5_b = md5_text(a_text), md5_text(b_text)
    _write(out / "A.md5", f"{md5_a}  A.canon\n")
    _write(out / "B.md5", f"{md5_b}  B.canon\n")
    diff = diff_rows(a_lines, b_lines)
    if diff:
        _write(out / "diff.tsv", "mint\tA_line\tB_line\n" + "".join(f"{m}\t{x}\t{y}\n" for m, x, y in diff))
    b_picks = sorted(ln.split("\t", 1)[0] for ln in b_lines if ln.split("\t")[1] == "pick")
    a_picks = sorted(ln.split("\t", 1)[0] for ln in a_lines if ln.split("\t")[1] == "pick")

    # C
    c: dict[str, Any] = {"skipped": True}
    md5_c = md5_bu = None
    equal_c: bool | None = None
    if not skip_c:
        t = time.monotonic()
        scorer_root = Path(scorer_repo) if scorer_repo is not None else repo
        if scorer is None:
            def scorer(book: str, picks: Path, odir: Path, _sr: Path = scorer_root) -> tuple[list[str], list[str]]:  # type: ignore[misc]
                return subprocess_scorer(_sr, view, roots, day, picks, odir, book, scorer_extra, out / f"scorer_{book}.log")
        all_mints, cmd_all = scorer("all", b_path, out / "scorer_all")
        wall["C_all"] = round(time.monotonic() - t, 1)
        t = time.monotonic()
        pick_mints, cmd_picks = scorer("picks", b_path, out / "scorer_picks")
        wall["C_picks"] = round(time.monotonic() - t, 1)
        universe = set(all_mints)
        c_list = sorted(pick_mints)
        bu = sorted(m for m in b_picks if m in universe)
        c_text, bu_text = "".join(m + "\n" for m in c_list), "".join(m + "\n" for m in bu)
        _write(out / "C.list", c_text)
        _write(out / "Bpicks_in_U.list", bu_text)
        md5_c, md5_bu = md5_text(c_text), md5_text(bu_text)
        _write(out / "C.md5", f"{md5_c}  C.list\n")
        _write(out / "Bpicks_in_U.md5", f"{md5_bu}  Bpicks_in_U.list\n")
        equal_c = md5_c == md5_bu
        c = {"skipped": False, "n_universe": len(universe), "n_attempt_rows_all": len(all_mints), "n_attempt_rows_picks": len(pick_mints),
             "n_C": len(c_list), "n_Bpicks_in_U": len(bu), "n_Bpicks_not_in_U": len(b_picks) - len(bu),
             "scorer_repo": str(scorer_root), "scorer_extra_args": list(scorer_extra), "cmd_all": cmd_all, "cmd_picks": cmd_picks}
        sr_state = scorer_state or (state if scorer_root.resolve() == Path(repo).resolve() else None)
        if sr_state is not None:
            c["scorer_head"] = sr_state["head"]
            try:
                c["scorer_blob"] = head_blob(scorer_root, SCORER_PATH)
            except E0Error:
                c["scorer_blob"] = None

    boots = bmeta.get("boots") or [{}]
    checks = {
        "pinned_blobs_are_the_imported_modules": all(imported[m]["blob"] == pins["blobs"][m] for m in PINNED_MODULES),
        "frozen_md5": pins["frozen_md5"]["ok"],
        "equal_AB": md5_a == md5_b,
        "boot_history_equal": astats["history_rows"] == boots[0].get("history_rows") and astats["staged_files"] == boots[0].get("staged_files"),
        "A_log_rows_equal_engine_rows": len(a_rows) == astats["engine_gate_rows"],
        "nonempty": bool(a_lines) and bool(b_picks),
        "equal_C": equal_c if equal_c is not None else False,
    }
    wall["total"] = round(time.monotonic() - t_start, 1)
    e0: dict[str, Any] = {
        "schema": SCHEMA, "view": view, "day": day, "commit": state["head"], "origin_branches": state["origin_branches"],
        "view_sha256": view_sha, "md5_A": md5_a, "md5_B": md5_b, "equal_AB": md5_a == md5_b, "md5_C": md5_c, "md5_Bpicks_U": md5_bu, "equal_C": equal_c,
        "n_A": len(a_lines), "n_B": len(b_lines), "n_picks": len(b_picks), "n_picks_A": len(a_picks), "n_dead_B": n_dead, "n_diff": len(diff),
        "blobs": pins["blobs"], "frozen_md5": pins["frozen_md5"], "imported": imported,
        "A": astats, "B": {k: bmeta.get(k) for k in ("wall_s", "boots", "create_rows_t_recv_imputed", "trade_rows_t_recv_imputed", "tx_index_null_view",
                                                      "daily_restart", "prune_every", "create_time")},
        "C": c, "wall_s": wall, "checks": checks, "ok": all(checks.values()),
        "python": platform.python_version(), "labels": "A: entered->pick, reason below_threshold->below, else reason (null->unknown); B: the decision field",
    }
    _write(out / "e0.json", json.dumps(e0, indent=2, sort_keys=True) + "\n")
    return e0


# ---- CLI ----------------------------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    e0 = run_e0(args.view, args.day, Path(args.out), scorer_repo=Path(args.scorer_repo) if args.scorer_repo else None,
                scorer_extra=args.scorer_arg or (), skip_c=args.skip_c)
    keys = ("view", "day", "commit", "md5_A", "md5_B", "equal_AB", "md5_C", "md5_Bpicks_U", "equal_C", "n_A", "n_B", "n_picks", "n_diff", "checks", "wall_s", "ok")
    print(json.dumps({k: e0[k] for k in keys}, indent=2, sort_keys=True))
    return 0 if e0["ok"] else 1


def cmd_check(args: argparse.Namespace) -> int:
    e0 = json.loads(Path(args.e0).read_text(encoding="utf-8"))
    if e0.get("schema") != SCHEMA:
        raise E0Error(f"{args.e0}: not a {SCHEMA} file")
    res = verify_pins(e0, Path(args.worktree))
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="A, B and C for one exploration view-day")
    r.add_argument("--view", required=True, choices=sorted(cp.BLOCKS))
    r.add_argument("--day", required=True, help="UTC day YYYY-MM-DD")
    r.add_argument("--out", required=True, help="empty or new directory")
    r.add_argument("--scorer-repo", help="a clean git tree on origin holding tools/cap_pick_score.py, if it is not this tree (default: this tree)")
    r.add_argument("--scorer-arg", action="append", help="an extra scorer argument for both runs (repeatable), e.g. a read-ready flag")
    r.add_argument("--skip-c", action="store_true", help="development only: skip the scorer; the run then reports ok=false")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("check", help="re-verify the recorded blob shas against HEAD and the working copy of a worktree")
    c.add_argument("e0", help="e0.json")
    c.add_argument("--worktree", default=str(REPO))
    c.set_defaults(fn=cmd_check)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (E0Error, cp.Refused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
