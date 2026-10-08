"""EXP-022 E0 harness: md5 decision-equivalence of the live-gate replay, the runner replay and the scorer (EXP/EXP-022-cap-pick-part1-prereg.md 2.1).

Exploration pools only. One UTC day of one exploration view, never forward-walk, walk-2 or any sealed block. Offline, no network, no key.

    python -m tools.cap_pick_e0 run   --view explore-0814 --day 2026-08-20 --out DIR          (the E0 record: E0_VIEW, E0_DAY)
    python -m tools.cap_pick_e0 run   --view V --day D --out DIR --dry-run [--scorer-arg ...]   (anything else)
    python -m tools.cap_pick_e0 check DIR/e0.json [--worktree PATH]

THE THREE LISTS (all for the same day; both sides boot at 00:00Z of that day)
  A  the runner's own replay path, `forward_paper.replay_rows`, with the frozen gated EXP-012 book, preloading creator history with
     `exp012_boot=(staged creates, 00:00Z)` (the hook that makes `replay_rows` boot as `main` does). Decisions are read from A's `exp012_gate` JsonlLog.
  B  `cap_pick_gate_replay.replay_view` for the day (what `cap_pick_gate_replay replay --view V --from-day D --to-day D` calls), daily restart on.
     B's file is written in the CLI's own layout (meta line, then `kind: decision` and `kind: dead` rows) and is the file the scorer reads.
  C  the scorer's EXP-022 mode, run ONCE: `tools/cap_pick_score.py --exp022 --exp022-source exploration --book picks --picks <B file> --only-day D` plus the
     narrowest source flags that cover the view (its roots, from `cap_pick_gate_replay.BLOCKS`). `--exp022` forbids `--book all`, so U is no longer a second run:
     C = the attempt mints in `rows.csv` (the pick attempts); U = the attempt mints in `universe.csv` (every mint the universe function saw, attempt or the
     reason it is not); C must equal, by md5, B's `pick` mints inside U. `--exp022`, `--exp022-source exploration` and `--book picks` are module constants
     (`SCORER_FLAGS`), never CLI-overridable: a `--scorer-arg` that names or abbreviates one of them (or `--picks`, `--only-day`, `--out-dir`) is refused even in a
     dry run. The scorer's `summary.json` constants, source and adapter, universe counts, `picks_in_input` and `picks_not_attempts` (by reason) are copied into
     `e0.json` (`scorer_summary`); nothing of its P&L is read or copied (the harness reads only the `mint`, `status` and `reason` columns of its CSVs).
  Canonical decision list: one line per mint, sorted by mint, `mint<TAB>decision<TAB>mig_ms<TAB>repr(score)`, an empty field when the score is null.
  Both canonical files are built by `canon_lines`. Two versions of the A and B lists are written and hashed:
    full    every decided mint (`A.canon`, `B.canon`; `md5_A_full`, `md5_B_full`, `equal_full`). REPORT ONLY.
    decide  the deciding set D, the same set of mints for A and for B (`A.decide.canon`, `B.decide.canon`; `md5_A_decide`, `md5_B_decide`, `equal_decide`):
              * every mint with `mig_ms - create_ms <= DROP_AFTER_CREATE_MS` (60 min, imported from `forward_exp012_gate`) on EITHER side, each side using its
                own create time; PLUS
              * every mint B decides `pick`, whatever its age.
            A mint of D that one side lacks is a line missing on that side, so the md5s differ.
  Create-time sources (the create time the side's gate used for `time_to_migrate`):
    B  its decision record's `create_ms` (the gate accumulator's create time: the create row's second, or the first matching bonding print's chain second).
    A  the same quantity from A's gate row: `mig_ms - time_to_migrate_s*1000` when the row has features; else the CreateSignal's raw `t_signal_ms`, which is B's
       fallback too (`Replayer._on_signal`: `m.create.t_signal_ms` when the accumulator is gone) and what EXP-022 Amendment 1 specifies.
  Disclosed: for a no-feature row where B's accumulator still exists (no_bond_history, gate_error) B's `create_ms` is the accumulator's floor-second while A
  uses the raw `t_signal_ms`; they differ only on a tape with sub-second creates (explore-0814 creates are whole seconds).
  A create-time disagreement on any mint of D (a mint both sides decided) is a mismatch: it goes to `diff.tsv` and `equal_decide` is false.
  Mints older than 60 min at migration on either side: a crosstab of A label x B label in `e0.json` (`crosstab_gt60`) and their lines in `diff_gt60.tsv` (a column
  says which of them are in D: the B picks).
  Why the age cut: the 60-minute skip (gate note 5) is done by `Exp012Online.prune`, which the engine calls from `_at_time` when `_prints % every == 0` at a
  step end, `every` = 5,000 with a live clock (the live runner: ms steps, so within about a minute of the 60-minute mark; B does the same) and 50,000
  without one (`replay_rows`: second steps, so it almost never fires). A scoring a mint older than 60 min is therefore a replay artifact, not live behaviour.
  The B picks are added so that a mint B picks at any age must be decided the same way by A.
  Dry run 2026-08-17 on explore-0814 (commit 71d03c2): all 56 full-list differences were such mints.
  E0_CRITERION = "le60_either_plus_B_picks" (a module constant, not a flag; it follows the EXP-022 E0 amendment). Exit 0 iff `equal_decide` AND `equal_C`
  AND the pins check pass (and the sanity checks below). `equal_full` decides nothing. md5(C list) must equal md5(B pick mints in U).

PINS (EXP-022 2.1 item 4)
  Git blob shas at HEAD of the four pinned modules, the md5 of ARTIFACTS/exp012/FROZEN.md5 (must be a01f05dfb1e622f78b2bba55d174be09), and a check that the
  files Python actually imported hash to those blobs. `run` refuses unless the git tree is clean (untracked files count) and HEAD is contained in a remote
  branch `origin/*` known locally (no fetch is done: no network). `check` repeats the pin comparison against HEAD, and the working copy, of any worktree.

SURFACE USED FROM tools/cap_pick_gate_replay (nothing else): Block, BLOCKS, SCHEMA, Refused, build_engine, replay_view(block, days, engine=, roots=, log=),
hour_files, stage_creates, iter_json_rows, iter_lines, quick_mint, create_signal_from_row, check_days; load_online in the tests only.

IMPORTED MODULES (quant-proof edit on #473). After A, B and the scorer have run, `e0.json` records `imported_module_blobs` (module name -> git blob sha at HEAD) for
every `tools.*` module actually imported: by A and B (read from `sys.modules`; the file Python loaded) and by the scorer (its two runs use `-X importtime`; the
`tools.*` names are read from the logs, plus the scorer module itself). Each is compared with `git rev-parse HEAD:<path>` and with the hash of the file as
loaded; any mismatch, a module outside the tree or an untracked module fails `ok` (`imported_module_mismatches`). `imported_module_sides` says which side
imported each. If the scorer runs from another tree (`--scorer-repo`, dry runs only) its modules are recorded and checked in that tree, under
`scorer_imported_module_blobs`. `check e0.json` verifies every recorded module against HEAD and the working copy of the given worktree (the scorer's
too, with `--scorer-worktree`), not only the four pinned ones. `Bpicks_not_in_U` lists the B pick mints the scorer's universe lacks (report only).

SANITY CHECKS (also required for exit 0, so an equal result cannot be vacuous or built on different inputs): the scorer read as many picks as B decided
(`picks_in_input` = B's pick count); A's preload row count and staged file count
equal B's; A's gate log rows equal the engine's rows; the deciding set D and B's picks are not empty; and the scorer's `--book picks` list C has at
least one mint (`n_C >= 1`: with an empty scorer universe C and B's picks in U are both empty, and their md5s match). `check` requires the recorded `n_C > 0`.

MEMORY: A peaked at 13.3 GB (sampled, lower bound) on 08-17; run E0 as a MiScusi job with mem 28 GB. `replay_rows` queues every print in the engine inbox
before it drains (2 h of that day took 781 MB, 5 h took 2.1 GB), so A does not fit in 3 GB. B needs about 2 GB; the scorer is a subprocess in the same cgroup.

THE E0 RECORD IS PINNED (quant-proof and reviewer edits on #473)
  * `E0_VIEW` and `E0_DAY` are module constants (explore-0814, 2026-08-20). `run` refuses any other view or day unless `--dry-run` is given. A dry run writes
    `"dry_run": true` in `e0.json`, and `check` refuses a dry-run file as the E0 record (exit 2).
  * `--scorer-arg` is refused unless `--dry-run`: an extra scorer argument could carry a sealed or forward path.
  * `--scorer-repo` is refused unless `--dry-run`: the E0 record runs the scorer from the same clean HEAD as everything else (EXP-022 2.1 item 3 names the
    read-ready scorer at the E0 commit), and a non-dry run is refused unless `tools/cap_pick_score.py` exists at HEAD.
  * `check` does not trust the stored `ok`. It recomputes it from the recorded fields: `md5_A_decide == md5_B_decide` with no create-time disagreement,
    `md5_C == md5_Bpicks_U`, the recorded sanity checks, the view and day equal to E0_VIEW and E0_DAY, `dry_run` false, the criterion name, no
    `imported_module_mismatches`, and the blob and module comparisons it makes itself against the worktree. The stored `ok` is shown beside the result.

Exit codes: 0 the criterion and the sanity checks hold; 1 a check failed (A != B on D, C != B picks in U, ...); 2 refused or a step could not run.
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
from tools.forward_exp012_gate import DROP_AFTER_CREATE_MS

REPO = Path(__file__).resolve().parent.parent
SCHEMA = "cap_pick_e0_v1"
E0_CRITERION = "le60_either_plus_B_picks"  # the A-vs-B set that decides exit 0; the full list is report only (EXP-022 E0 amendment)
E0_VIEW = "explore-0814"  # the E0 record is this view on this day; anything else needs --dry-run
E0_DAY = "2026-08-20"
DAY_MS = 86_400_000
PINNED_MODULES = ("tools/forward_exp012_gate.py", "tools/forward_paper.py", "tools/exploration_entry_model.py", "tools/cap_pick_gate_replay.py")
FROZEN_MD5_PATH = "ARTIFACTS/exp012/FROZEN.md5"
FROZEN_MD5_EXPECTED = "a01f05dfb1e622f78b2bba55d174be09"
SCORER_PATH = "tools/cap_pick_score.py"
# The scorer mode E0 runs, as constants. Never a CLI option; `check_scorer_extra` refuses any --scorer-arg that names or abbreviates one of the PROTECTED flags.
SCORER_SOURCE_NAME = "exploration"
SCORER_FLAGS = ("--exp022", "--exp022-source", SCORER_SOURCE_NAME, "--book", "picks")
SCORER_PROTECTED = ("--exp022", "--exp022-source", "--book", "--picks", "--only-day", "--out-dir")
# data-coverage counts copied from the scorer summary (no outcome count, no P&L)
SUMMARY_COUNT_KEYS = ("hours", "migrations", "mints_with_canonical_pool", "censored", "multipool", "bad_json", "skipped_incomplete_migrations", "bad_reserves",
                      "attempts", "pick_attempts")
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


def _day_start_ms(day: str) -> int:
    import calendar

    return calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000


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


def loaded_tools_modules() -> dict[str, Path]:
    """Every `tools.*` module in `sys.modules` that has a file: name -> the file Python loaded."""
    return {n: Path(m.__file__).resolve() for n, m in list(sys.modules.items()) if n.startswith("tools.") and getattr(m, "__file__", None)}


def module_rel_in_tree(name: str, root: Path) -> str:
    """The repo path of module `name` in `root`: `tools/x.py`, else `tools/x/__init__.py`; the first form if neither exists."""
    base = name.replace(".", "/")
    if not (root / f"{base}.py").is_file() and (root / base / "__init__.py").is_file():
        return f"{base}/__init__.py"
    return f"{base}.py"


_IMPORTTIME = re.compile(r"^import time:\s+\d+\s*\|\s*\d+\s*\|\s*(\S+)\s*$")


def modules_from_importtime(text: str) -> set[str]:
    """The `tools.*` module names in a `python -X importtime` log."""
    out = set()
    for line in text.splitlines():
        m = _IMPORTTIME.match(line)
        if m and m.group(1).startswith("tools."):
            out.add(m.group(1))
    return out


def _check_module(root: Path, rel: str, file: Path) -> tuple[str | None, str | None]:
    """(blob at HEAD of `rel` in `root`, hash of `file` as loaded); None where absent."""
    try:
        head: str | None = head_blob(root, rel)
    except E0Error:
        head = None
    return head, (file_blob(root, file) if file.is_file() else None)


def collect_imported_modules(repo: Path, loaded: dict[str, Path], scorer_names: Iterable[str] = (), scorer_root: Path | None = None) -> dict[str, Any]:
    """Blob shas at HEAD of every imported `tools.*` module. `loaded` is A's and B's (name -> file loaded); `scorer_names` the scorer's, resolved in
    `scorer_root` (default: `repo`; a different tree is recorded apart). A module whose loaded file does not hash to its HEAD blob is a mismatch."""
    blobs: dict[str, str | None] = {}
    sides: dict[str, list[str]] = {}
    scorer_blobs: dict[str, str | None] = {}
    bad: list[dict[str, Any]] = []
    for name, f in sorted(loaded.items()):
        rel = f"{name.replace('.', '/')}/__init__.py" if f.name == "__init__.py" else f"{name.replace('.', '/')}.py"
        head, disk = _check_module(repo, rel, f)
        blobs[name] = head
        sides.setdefault(name, []).append("A_B")
        if head is None or head != disk:
            bad.append({"module": name, "side": "A_B", "path": rel, "head_blob": head, "file_blob": disk})
    sroot = Path(scorer_root) if scorer_root is not None else Path(repo)
    same = sroot.resolve() == Path(repo).resolve()
    for name in sorted(set(scorer_names)):
        rel = module_rel_in_tree(name, sroot)
        head, disk = _check_module(sroot, rel, sroot / rel)
        (blobs if same else scorer_blobs)[name] = head
        sides.setdefault(name, []).append("scorer")
        if head is None or head != disk:
            bad.append({"module": name, "side": "scorer", "path": rel, "head_blob": head, "file_blob": disk})
    return {"blobs": blobs, "scorer_blobs": scorer_blobs, "sides": sides, "mismatches": bad}


def verify_imported_modules(e0: dict[str, Any], worktree: Path, scorer_worktree: Path | None = None) -> dict[str, Any]:
    """Every recorded module against HEAD and the working copy of `worktree` (the scorer-only ones against `scorer_worktree`)."""
    recorded = e0.get("imported_module_blobs")
    if not isinstance(recorded, dict) or not recorded:
        return {"ok": False, "n": 0, "bad": [{"module": None, "why": "e0.json records no imported_module_blobs"}]}
    bad: list[dict[str, Any]] = []

    def one(root: Path, name: str, want: Any) -> None:
        rel = module_rel_in_tree(name, root)
        head, disk = _check_module(root, rel, root / rel)
        if want is None or head != want or disk != want:
            bad.append({"module": name, "path": rel, "recorded": want, "head": head, "worktree_file": disk})

    for name, want in recorded.items():
        one(Path(worktree), name, want)
    srec = e0.get("scorer_imported_module_blobs") or {}
    if srec and scorer_worktree is None:
        bad.append({"module": None, "why": "scorer_imported_module_blobs is recorded: pass --scorer-worktree"})
    elif srec:
        for name, want in srec.items():
            one(Path(scorer_worktree), name, want)
    return {"ok": not bad, "n": len(recorded) + len(srec), "bad": bad}


SANITY_KEYS = ("boot_history_equal", "A_log_rows_equal_engine_rows", "nonempty", "scorer_picks_in_input")


def recompute_ok(e0: dict[str, Any]) -> dict[str, Any]:
    """The parts of `ok` that follow from the recorded fields alone. The stored `ok` is never read."""
    chk = e0.get("checks") or {}
    parts = {
        "equal_decide": e0.get("md5_A_decide") is not None and e0.get("md5_A_decide") == e0.get("md5_B_decide") and e0.get("n_create_ms_disagree") == 0,
        "equal_C": e0.get("md5_C") is not None and e0.get("md5_C") == e0.get("md5_Bpicks_U"),
        "sanity": all(chk.get(k) is True for k in SANITY_KEYS),
        "scorer_mode": e0.get("scorer_flags") == list(SCORER_FLAGS) and (e0.get("scorer_summary") or {}).get("mode") == "exp022"
        and (e0.get("scorer_summary") or {}).get("source") == SCORER_SOURCE_NAME,
        "n_C_positive": isinstance(e0.get("n_C"), int) and not isinstance(e0.get("n_C"), bool) and e0["n_C"] >= 1,
        "scope": e0.get("view") == E0_VIEW and e0.get("day") == E0_DAY,
        "not_a_dry_run": e0.get("dry_run") is False,
        "criterion": e0.get("e0_criterion") == E0_CRITERION,
        "no_import_mismatch": e0.get("imported_module_mismatches") == [],
    }
    return {"parts": parts, "ok": all(parts.values())}


def verify_pins(e0: dict[str, Any], worktree: Path, scorer_worktree: Path | None = None) -> dict[str, Any]:
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
    imports = verify_imported_modules(e0, worktree, scorer_worktree)
    rec = recompute_ok(e0)
    return {"worktree": str(worktree), "head": _git(worktree, "rev-parse", "HEAD").strip(), "recorded_commit": e0.get("commit"),
            "stored_ok": e0.get("ok"), "recomputed": rec, "modules": mods, "frozen_md5": frozen, "imported_modules": imports,
            "ok": all(m["ok"] for m in mods.values()) and frozen["ok"] and imports["ok"] and rec["ok"]}


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




def a_side_rows(gate_rows: Iterable[dict[str, Any]], signal_ms: dict[str, int]) -> list[Any]:
    """A's decisions with the create time A's gate used: `mig_ms - time_to_migrate_s*1000` from the gate row's features, else the
    CreateSignal's raw `t_signal_ms`, which is B's fallback (a row with no features: no_features, no_bond_history, gate_error)."""
    out = []
    for r in gate_rows:
        m = r["mint"]
        if m not in signal_ms:
            raise E0Error(f"A decided mint {m} that has no CreateSignal")
        ttm = (r.get("features") or {}).get("time_to_migrate_s")
        if isinstance(ttm, (int, float)) and not isinstance(ttm, bool):
            create = r["mig_ms"] - round(ttm * 1000)
        else:
            create = signal_ms[m]
        out.append((m, label_from_gate_row(r), r["mig_ms"], r["score"], create))
    return out


def b_side_rows(records: Iterable[dict[str, Any]]) -> tuple[list[Any], int]:
    """B's `kind: decision` rows with B's `create_ms`. `dead` rows are dropped (returned as a count); the meta line is not a decision."""
    keep, dead = [], 0
    for r in records:
        k = r.get("kind")
        if k == "decision":
            keep.append((r["mint"], r["decision"], r["mig_ms"], r["score"], r.get("create_ms")))
        elif k == "dead":
            dead += 1
    return keep, dead


def is_le60(row: Any, side: str) -> bool:
    """`mig_ms - create_ms <= DROP_AFTER_CREATE_MS`, the gate's own edge (`prune` drops a mint when the age is strictly greater)."""
    if row[4] is None:
        raise E0Error(f"side {side} has no create time for mint {row[0]}")
    return row[2] - row[4] <= DROP_AFTER_CREATE_MS


def _lines(rows: Iterable[Any], side: str) -> list[str]:
    return canon_lines(((m, lab, mig, sc) for m, lab, mig, sc, _c in rows), side)


def compare_sides(a_rows: Sequence[Any], b_rows: Sequence[Any]) -> dict[str, Any]:
    """Full and deciding-set canonical lists of both sides, create-time disagreements in the set, and the mints older than 60 min with their label crosstab.
    The deciding set D: mints le60 on either side (each side's own create time) plus every mint B decides `pick`."""
    by_a, by_b = {r[0]: r for r in a_rows}, {r[0]: r for r in b_rows}
    d_set = ({r[0] for r in a_rows if is_le60(r, "A")} | {r[0] for r in b_rows if is_le60(r, "B")} | {r[0] for r in b_rows if r[1] == "pick"})
    a_full, b_full = _lines(a_rows, "A"), _lines(b_rows, "B")
    a_dec = _lines([r for r in a_rows if r[0] in d_set], "A")
    b_dec = _lines([r for r in b_rows if r[0] in d_set], "B")
    disagree = sorted(m for m in d_set if m in by_a and m in by_b and by_a[m][4] != by_b[m][4])
    gt = sorted({r[0] for r in a_rows if not is_le60(r, "A")} | {r[0] for r in b_rows if not is_le60(r, "B")})
    cross: dict[str, dict[str, int]] = {}
    for m in gt:
        la = by_a[m][1] if m in by_a else "<absent>"
        lb = by_b[m][1] if m in by_b else "<absent>"
        cross.setdefault(la, {})
        cross[la][lb] = cross[la].get(lb, 0) + 1
    ln_a = {x.split("\t", 1)[0]: x for x in a_full}
    ln_b = {x.split("\t", 1)[0]: x for x in b_full}
    gt_rows = [(m, ln_a.get(m, "<absent>").replace("\t", "|"), ln_b.get(m, "<absent>").replace("\t", "|"),
                by_a[m][4] if m in by_a else "", by_b[m][4] if m in by_b else "", "yes" if m in d_set else "no") for m in gt]
    return {"a_full": a_full, "b_full": b_full, "a_decide": a_dec, "b_decide": b_dec, "n_decide_set": len(d_set), "diff_decide": diff_rows(a_dec, b_dec),
            "create_disagree": [(m, by_a[m][4], by_b[m][4]) for m in disagree], "gt60": gt_rows, "crosstab_gt60": cross}


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
               *, boot: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    """A: the runner's `replay_rows` on the day. Returns the gate rows read back from its `exp012_gate` JsonlLog, a stats dict and the
    CreateSignal time (`t_signal_ms`) of every mint A was given."""
    from tools.forward_paper import JsonlLog, replay_rows

    boot_ms = _day_start_ms(day)
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
    return rows, stats, {c.mint: c.t_signal_ms for c in creates}


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


def check_scorer_extra(extra: Sequence[str]) -> None:
    """Refuse a --scorer-arg that names, or is an abbreviation of, a protected scorer flag. The mode and the book are constants."""
    for a in extra:
        if not a.startswith("--"):
            continue
        name = a.split("=", 1)[0]
        if any(name == p or (len(name) >= 3 and p.startswith(name)) for p in SCORER_PROTECTED):
            raise E0Error(f"--scorer-arg {a!r} names or abbreviates a flag E0 fixes ({', '.join(SCORER_PROTECTED)})")


def scorer_cmd(view: str, roots: Sequence[str], day: str, picks: Path, out_dir: Path, extra: Sequence[str]) -> list[str]:
    return [sys.executable, "-X", "importtime", "-m", SCORER_MODULE, *scorer_source_flags(view, roots), "--only-day", day, *SCORER_FLAGS, "--picks", str(picks),
            "--out-dir", str(out_dir), *extra]


def read_rows_mints(path: Path) -> list[str]:
    """The `mint` column of rows.csv, and nothing else (the other columns are the scorer's P&L)."""
    with path.open(newline="", encoding="utf-8") as fh:
        return [r["mint"] for r in csv.DictReader(fh)]


def read_universe_csv(path: Path) -> tuple[list[str], dict[str, int]]:
    """(attempt mints, excluded count by reason) from universe.csv: only `mint`, `status` and `reason` are read."""
    attempts: list[str] = []
    excluded: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["status"] == "attempt":
                attempts.append(r["mint"])
            elif r["status"] == "excluded":
                excluded[r["reason"]] = excluded.get(r["reason"], 0) + 1
            else:
                raise E0Error(f"{path}: unknown status {r['status']!r} for mint {r['mint']}")
    return attempts, dict(sorted(excluded.items()))


def scorer_summary_record(summary: dict[str, Any]) -> dict[str, Any]:
    """The part of the scorer's summary.json that goes into e0.json: mode, source, constants, universe and pick accounting, data-coverage counts. No P&L, no
    book statistics, no cell, no fill or exit count. Refuses a summary that is not the EXP-022 exploration mode."""
    ex = summary.get("exp022") or {}
    if summary.get("mode") != "exp022" or ex.get("source") != SCORER_SOURCE_NAME:
        raise E0Error(f"scorer summary is mode {summary.get('mode')!r} source {ex.get('source')!r}; E0 needs exp022 / {SCORER_SOURCE_NAME}")
    uni = ex.get("universe") or {}
    counts = summary.get("counts") or {}
    return {"mode": summary["mode"], "source": ex["source"], "adapter": ex.get("adapter"), "constants": ex.get("constants"), "flags_pinned": ex.get("flags_pinned"),
            "universe": {"attempts_in_universe": uni.get("attempts_in_universe"), "pick_attempts": uni.get("pick_attempts"),
                         "excluded_by_reason": uni.get("excluded_by_reason"),
                         "tape_coverage_short": len((uni.get("report_only") or {}).get("tape_coverage_short") or []),
                         "n_missing_v0": (uni.get("missing_v0") or {}).get("n_missing_v0")},
            "picks_in_input": summary.get("picks_in_input"), "picks_not_attempts": summary.get("picks_not_attempts"),
            "picks_sha256": (summary.get("picks") or {}).get("sha256"), "vmap": summary.get("vmap"), "sources": summary.get("sources"), "days": summary.get("days"),
            "counts": {k: counts.get(k) for k in SUMMARY_COUNT_KEYS}}


def subprocess_scorer(scorer_repo: Path, view: str, roots: Sequence[str], day: str, picks: Path, out_dir: Path, extra: Sequence[str],
                      log_path: Path) -> dict[str, Any]:
    """Run the scorer once in `scorer_repo`. Returns {"attempts": rows.csv mints in file order, "universe": attempt mints of universe.csv,
    "excluded": count by reason, "summary": scorer_summary_record(...), "cmd": the command}."""
    cmd = scorer_cmd(view, roots, day, picks, out_dir, extra)
    with log_path.open("w", encoding="utf-8") as lf:
        r = subprocess.run(cmd, cwd=str(scorer_repo), stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise E0Error(f"scorer exited {r.returncode} (see {log_path})")
    for name in ("rows.csv", "universe.csv", "summary.json"):
        if not (out_dir / name).is_file():
            raise E0Error(f"scorer wrote no {name} in {out_dir}")
    universe, excluded = read_universe_csv(out_dir / "universe.csv")
    summary = scorer_summary_record(json.loads((out_dir / "summary.json").read_text(encoding="utf-8")))
    return {"attempts": read_rows_mints(out_dir / "rows.csv"), "universe": universe, "excluded": excluded, "summary": summary, "cmd": cmd}


Scorer = Callable[[Path, Path], "dict[str, Any]"]  # (picks file, out dir) -> subprocess_scorer's result


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


def check_scope(view: str, day: str, dry_run: bool, scorer_extra: Sequence[str] = (), scorer_repo: Path | None = None) -> None:
    """The E0 record is E0_VIEW on E0_DAY with the scorer's own flags; anything else, or an extra scorer argument, is a dry run."""
    if dry_run:
        return
    if view != E0_VIEW or day != E0_DAY:
        raise E0Error(f"the E0 record is {E0_VIEW} {E0_DAY}; {view} {day} needs --dry-run")
    if scorer_extra:
        raise E0Error("--scorer-arg needs --dry-run (an extra scorer argument could carry a sealed or forward path)")
    if scorer_repo is not None:
        raise E0Error("--scorer-repo needs --dry-run (the E0 record runs the scorer from the same clean HEAD as the rest)")


def run_e0(view: str, day: str, out: Path, *, block: cp.Block | None = None, engine_factory: Callable[[], Any] | None = None,
           scorer: Scorer | None = None, repo: Path | None = None, scorer_repo: Path | None = None, scorer_extra: Sequence[str] = (),
           skip_c: bool = False, dry_run: bool = False, log: Any = sys.stderr) -> dict[str, Any]:
    """The whole E0 for one view-day. Writes A.canon, B.canon, B.jsonl, C.list, Bpicks_in_U.list, the md5 files, diff.tsv (if A != B) and e0.json."""
    t_start = time.monotonic()
    check_scope(view, day, dry_run, scorer_extra, scorer_repo)
    check_scorer_extra(scorer_extra)
    repo = repo or REPO
    if not dry_run and not skip_c and scorer is None:
        try:
            head_blob(repo, SCORER_PATH)
        except E0Error:
            raise E0Error(f"non-dry run: {SCORER_PATH} must exist at HEAD of {repo} (the scorer runs from the same clean HEAD)") from None
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
    b_rows, n_dead = b_side_rows(recs)
    del recs

    # A
    t = time.monotonic()
    files = cp.hour_files(block, roots)
    specs = [run.spec for run in engine_factory().books]
    a_gate_rows, astats, a_signal_ms = run_side_a(block, files, roots, day, specs, out)
    wall["A"] = round(time.monotonic() - t, 1)
    a_rows = a_side_rows(a_gate_rows, a_signal_ms)
    del a_signal_ms

    cmpd = compare_sides(a_rows, b_rows)
    a_lines, b_lines = cmpd["a_full"], cmpd["b_full"]
    texts = {"A.canon": canon_text(a_lines), "B.canon": canon_text(b_lines),
             "A.decide.canon": canon_text(cmpd["a_decide"]), "B.decide.canon": canon_text(cmpd["b_decide"])}
    md5s = {}
    for name, text in texts.items():
        _write(out / name, text)
        md5s[name] = md5_text(text)
        _write(out / (name[: -len("canon")] + "md5"), f"{md5s[name]}  {name}\n")
    md5_a, md5_b, md5_ad, md5_bd = md5s["A.canon"], md5s["B.canon"], md5s["A.decide.canon"], md5s["B.decide.canon"]
    diff = [(m, x, y) for m, x, y in cmpd["diff_decide"]] + [(m, f"create_ms={x}", f"create_ms={y}") for m, x, y in cmpd["create_disagree"]]
    if diff:
        _write(out / "diff.tsv", "mint\tA_line\tB_line\n" + "".join(f"{m}\t{x}\t{y}\n" for m, x, y in diff))
    if cmpd["gt60"]:
        _write(out / "diff_gt60.tsv", "mint\tA_line\tB_line\tA_create_ms\tB_create_ms\tin_decide\n" + "".join("\t".join(str(v) for v in row) + "\n" for row in cmpd["gt60"]))
    equal_full = md5_a == md5_b
    equal_decide = md5_ad == md5_bd and not cmpd["create_disagree"]
    b_picks = sorted(r[0] for r in b_rows if r[1] == "pick")
    a_picks = sorted(r[0] for r in a_rows if r[1] == "pick")

    # C
    c: dict[str, Any] = {"skipped": True}
    scorer_root: Path = Path(repo)
    bpicks_missing: list[str] = []
    scorer_summary: dict[str, Any] = {}
    not_attempts_by_reason: dict[str, int] = {}
    n_c = n_universe = 0
    md5_c = md5_bu = None
    equal_c: bool | None = None
    picks_in_input_ok = False
    if not skip_c:
        t = time.monotonic()
        scorer_root = Path(scorer_repo) if scorer_repo is not None else repo
        if scorer is None:
            def scorer(picks: Path, odir: Path, _sr: Path = scorer_root) -> dict[str, Any]:  # type: ignore[misc]
                return subprocess_scorer(_sr, view, roots, day, picks, odir, scorer_extra, out / "scorer_exp022.log")
        res = scorer(b_path, out / "scorer_exp022")
        wall["C"] = round(time.monotonic() - t, 1)
        universe = set(res["universe"])
        n_universe = len(universe)
        c_list = sorted(res["attempts"])
        bu = sorted(m for m in b_picks if m in universe)
        n_c = len(c_list)
        c_text, bu_text = "".join(m + "\n" for m in c_list), "".join(m + "\n" for m in bu)
        bpicks_missing = sorted(m for m in b_picks if m not in universe)
        _write(out / "Bpicks_not_in_U.list", "".join(m + "\n" for m in bpicks_missing))
        _write(out / "C.list", c_text)
        _write(out / "Bpicks_in_U.list", bu_text)
        md5_c, md5_bu = md5_text(c_text), md5_text(bu_text)
        _write(out / "C.md5", f"{md5_c}  C.list\n")
        _write(out / "Bpicks_in_U.md5", f"{md5_bu}  Bpicks_in_U.list\n")
        equal_c = md5_c == md5_bu
        scorer_summary = res["summary"]
        not_attempts_by_reason = {k: len(v) for k, v in sorted((scorer_summary.get("picks_not_attempts") or {}).items())}
        picks_in_input_ok = scorer_summary.get("picks_in_input") == len(b_picks)
        c = {"skipped": False, "n_universe": n_universe, "n_attempt_rows": len(res["attempts"]), "n_C": n_c, "n_Bpicks_in_U": len(bu),
             "n_Bpicks_not_in_U": len(bpicks_missing), "excluded_by_reason": res["excluded"],
             "scorer_repo": str(scorer_root), "scorer_extra_args": list(scorer_extra), "cmd": res["cmd"]}
        sr_state = scorer_state or (state if scorer_root.resolve() == Path(repo).resolve() else None)
        if sr_state is not None:
            c["scorer_head"] = sr_state["head"]
            try:
                c["scorer_blob"] = head_blob(scorer_root, SCORER_PATH)
            except E0Error:
                c["scorer_blob"] = None

    scorer_names: set[str] = set()
    scorer_logs = sorted(out.glob("scorer_*.log")) if not skip_c else []
    for lg in scorer_logs:
        scorer_names |= modules_from_importtime(lg.read_text(encoding="utf-8", errors="replace"))
    if scorer_logs:
        scorer_names.add(SCORER_MODULE)
    loaded = loaded_tools_modules()
    loaded.setdefault("tools.cap_pick_e0", Path(__file__).resolve())  # run as a script this module is __main__, and it is code under test
    mods = collect_imported_modules(repo, loaded, scorer_names, scorer_root if scorer_logs else None)
    boots = bmeta.get("boots") or [{}]
    pins_ok = all(imported[m]["blob"] == pins["blobs"][m] for m in PINNED_MODULES) and pins["frozen_md5"]["ok"]
    checks = {  # every one is required for exit 0; `equal_full` is report only
        "equal_decide": equal_decide,
        "equal_C": equal_c if equal_c is not None else False,
        "pins": pins_ok,
        "imported_modules": not mods["mismatches"],
        "boot_history_equal": astats["history_rows"] == boots[0].get("history_rows") and astats["staged_files"] == boots[0].get("staged_files"),
        "A_log_rows_equal_engine_rows": len(a_gate_rows) == astats["engine_gate_rows"],
        "nonempty": bool(cmpd["a_decide"]) and bool(b_picks) and n_c >= 1,
        "scorer_picks_in_input": picks_in_input_ok,
    }
    wall["total"] = round(time.monotonic() - t_start, 1)
    e0: dict[str, Any] = {
        "schema": SCHEMA, "view": view, "day": day, "commit": state["head"], "origin_branches": state["origin_branches"],
        "e0_criterion": E0_CRITERION, "drop_after_create_ms": DROP_AFTER_CREATE_MS, "dry_run": bool(dry_run),
        "e0_pin": {"view": E0_VIEW, "day": E0_DAY},
        "view_sha256": view_sha,
        "md5_A_decide": md5_ad, "md5_B_decide": md5_bd, "equal_decide": equal_decide,
        "md5_A_full": md5_a, "md5_B_full": md5_b, "equal_full": equal_full,
        "md5_C": md5_c, "md5_Bpicks_U": md5_bu, "equal_C": equal_c, "n_C": n_c, "n_universe": n_universe,
        "scorer_flags": list(SCORER_FLAGS), "scorer_summary": scorer_summary, "picks_not_attempts_by_reason": not_attempts_by_reason,
        "n_A_full": len(a_lines), "n_B_full": len(b_lines), "n_decide_set": cmpd["n_decide_set"], "n_A_decide": len(cmpd["a_decide"]), "n_B_decide": len(cmpd["b_decide"]),
        "n_gt60": len(cmpd["gt60"]), "n_gt60_in_decide": sum(1 for r in cmpd["gt60"] if r[5] == "yes"), "crosstab_gt60": cmpd["crosstab_gt60"],
        "n_diff_decide": len(cmpd["diff_decide"]),
        "n_create_ms_disagree": len(cmpd["create_disagree"]), "n_diff": len(diff),
        "n_picks": len(b_picks), "n_picks_A": len(a_picks), "n_dead_B": n_dead,
        "blobs": pins["blobs"], "frozen_md5": pins["frozen_md5"], "imported": imported,
        "imported_module_blobs": mods["blobs"], "imported_module_sides": mods["sides"], "scorer_imported_module_blobs": mods["scorer_blobs"],
        "imported_module_mismatches": mods["mismatches"], "n_imported_modules": len(mods["blobs"]) + len(mods["scorer_blobs"]),
        "Bpicks_not_in_U": bpicks_missing,
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
                scorer_extra=args.scorer_arg or (), skip_c=args.skip_c, dry_run=args.dry_run)
    keys = ("view", "day", "commit", "e0_criterion", "md5_A_decide", "md5_B_decide", "equal_decide", "md5_A_full", "md5_B_full", "equal_full", "md5_C", "md5_Bpicks_U",
            "equal_C", "n_C", "n_universe", "picks_not_attempts_by_reason", "n_A_full", "n_B_full", "n_decide_set", "n_gt60", "n_gt60_in_decide", "crosstab_gt60", "n_create_ms_disagree", "n_picks", "n_diff", "checks", "wall_s", "ok")
    print(json.dumps({k: e0[k] for k in keys}, indent=2, sort_keys=True))
    return 0 if e0["ok"] else 1


def cmd_check(args: argparse.Namespace) -> int:
    e0 = json.loads(Path(args.e0).read_text(encoding="utf-8"))
    if e0.get("schema") != SCHEMA:
        raise E0Error(f"{args.e0}: not a {SCHEMA} file")
    if e0.get("dry_run") is not False:
        raise E0Error(f"{args.e0}: dry_run is {e0.get('dry_run')!r}; only a run with dry_run false can be the E0 record")
    res = verify_pins(e0, Path(args.worktree), Path(args.scorer_worktree) if args.scorer_worktree else None)
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
    r.add_argument("--scorer-arg", action="append", help="an extra scorer argument for both runs (repeatable; write a flag as --scorer-arg=--k-mode); needs --dry-run")
    r.add_argument("--dry-run", action="store_true", help=f"required for any view or day but {E0_VIEW} {E0_DAY} and for --scorer-arg; marks e0.json dry_run, which `check` refuses")
    r.add_argument("--skip-c", action="store_true", help="development only: skip the scorer; the run then reports ok=false")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("check", help="re-verify the recorded blob shas against HEAD and the working copy of a worktree")
    c.add_argument("e0", help="e0.json")
    c.add_argument("--worktree", default=str(REPO))
    c.add_argument("--scorer-worktree", help="the tree the scorer ran from, if e0.json records scorer_imported_module_blobs (dry runs with --scorer-repo)")
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
