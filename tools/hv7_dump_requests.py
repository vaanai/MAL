#!/usr/bin/env python3
"""hv7_dump_requests: write the EXACT HV7 rank requests for every pre-clock hour (2026-10-21..23, H:02:00Z) to disk, for the offline rehearsal. Prints COUNTS only.

For each t_d the runner's own screen runs (`Hv7Runner.screen`: hv6_cards.scan, the vol_60m filter, cards through the runner's `_card` = hv6_cards.build_card at t_d,
the impact veto, N <= 8 by f.vol_60m, hours with fewer than 3 skipped). The set is then turned into two requests with tools.hv7_rank.build_rank_request(..., seq =
t_d // 1 h, perm_tag = "A" and "B"): the same builder, template, playbook, nonce and card text the runner sends at that t_d (the runner's L arm uses perm_tag None).
There is no engine and no model call (0 API); the tape is polled to t_d - 1 ms only, so no print, price or outcome after t_d exists in the pass. Same day filter
and loader restrictions as tools/hv7_p0_count.py (days 2026-10-21..23 only, the real tape directory only, its hour list filtered to the chosen days).

Output, under the flag --out which must resolve inside /data2/mal/hv7-rehearsal/ (anything else is refused, symlinks resolved):
  prompts/batch-NN.jsonl   6 calls per file (the last file may hold fewer); one line per call:
                           {chunk_id, set_id (= t_d ms), shuffle ("A"|"B"), card_ids (display order), system, user}
  manifest.json            counts, per-file sha256, per-call request_sha256, the playbook / template sha256s, the git head of the code (a dirty tree is refused),
                           the screen counters leakage_refused / card_refused / chain_none. Counts and hashes only.
ONE RUN: a non-empty --out is refused (no stale batch file, no second run into the same dir). CALL CAP in code: sets <= 24 per day and calls == 2 x sets, so at most
72 sets / 144 calls for the three days; --half-hour adds the :32 instants (<= 48 sets per day, 144 sets / 288 calls), to be used only if the hourly run gave fewer than
30 sets (DESIGN section 7 amendment); it REPLACES the hourly run. In --half-hour mode seq is t_d // 1 h at :02 (so the :02 requests are byte-identical to the hourly
run's) and t_d // 1 h + 100000 at :32.
Nothing here computes an outcome. Pre-clock structure, not evidence.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import hv6_cards as H  # noqa: E402
from tools import hv7_rank as RK  # noqa: E402
from tools import hv7_runner as R  # noqa: E402
from tools.arena_declog import utc_day  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.drill_hv6_item6 import FakeChain  # noqa: E402
from tools.drill_hv7_real_layout import DRILL_ROOT, END_EXCLUSIVE, ID_RE, Refused, day_ms, hour_scan, is_drill_root, parse_days  # noqa: E402
from tools.hv7_p0_count import NoModel, NullEngine  # noqa: E402

REAL_TAPE = H.TAPE_ROOT
OUT_ROOT = "/data2/mal/hv7-rehearsal"
SHUFFLES = ("A", "B")
CALLS_PER_FILE = 6


def check_out(path: str | os.PathLike) -> Path:
    """The output dir must resolve inside OUT_ROOT (and not be OUT_ROOT's parent or a path with '..' / a symlink out)."""
    p = Path(os.path.abspath(os.fspath(path)))
    real, root = os.path.realpath(p), os.path.realpath(OUT_ROOT)
    if ".." in Path(os.fspath(path)).parts or not real.startswith(root + os.sep):
        raise Refused("out_not_under_rehearsal_root")
    return p


HALF_SEQ_OFFSET = 100_000
SCREEN_COUNTERS = ("leakage_refused", "card_refused", "chain_none")


def seq_for(t_d_ms: int) -> int:
    return t_d_ms // R.HOUR_MS + (HALF_SEQ_OFFSET if t_d_ms % R.HOUR_MS == R.T_D_OFFSET_MS + R.HOUR_MS // 2 else 0)


def requests_for_set(t_d_ms: int, cards_in_screen_order: list[dict], tags: tuple = SHUFFLES) -> list[tuple[Any, RK.RankRequest]]:
    """The requests for one set, in `tags` order. tag None = the runner's own L request (a test compares it with what Hv7Runner sends)."""
    t_d = RK.iso_from_ms(t_d_ms)
    return [(tag, RK.build_rank_request(t_d, cards_in_screen_order, seq=seq_for(t_d_ms), perm_tag=tag)) for tag in tags]


def repo_head(repo: str | os.PathLike | None = None) -> str:
    """git rev-parse HEAD of the code; a dirty tracked tree is refused (the prompt and code are frozen at a pushed sha)."""
    cwd = str(repo) if repo is not None else str(Path(__file__).resolve().parent.parent)
    try:
        if subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip():
            raise Refused("dirty_tree")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise Refused("git_head_unavailable") from None
    if len(head) != 40:
        raise Refused("git_head_unavailable")
    return head


def enforce_caps(n_sets: int, n_calls: int, days: list[str], half_hour: bool) -> None:
    per_day = 48 if half_hour else 24
    if n_sets > per_day * len(days) or n_calls != 2 * n_sets or n_calls > 2 * per_day * len(days):
        raise Refused("call_cap")


def check_empty(out_dir: Path) -> None:
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise Refused("out_not_empty")


def collect_sets(days: list[str], root: Path, tape: Path, *, start_ms: int | None = None, end_ms: int | None = None,
                 half_hour: bool = False) -> list[tuple[int, list[dict]]]:
    return collect(days, root, tape, start_ms=start_ms, end_ms=end_ms, half_hour=half_hour)[0]


def collect(days: list[str], root: Path, tape: Path, *, start_ms: int | None = None, end_ms: int | None = None,
            half_hour: bool = False) -> tuple[list[tuple[int, list[dict]]], dict[str, int]]:
    start = day_ms(days[0]) if start_ms is None else start_ms
    end = day_ms(days[-1]) + 86_400_000 if end_ms is None else end_ms
    run = R.Hv7Runner(R.Hv7Config(root=str(root / "v7"), frozen_sha="dumpreq0000000", clock_start_ms=start, phantom_filter=True), ArenaState(tape, start_ms=0),
                      NullEngine(), NoModel(), FakeChain(), R.Hv7Guard(root / "v7"))
    run._ask = lambda rr, T: None           # type: ignore[method-assign]  # no model call of any kind
    want = frozenset(days)
    orig = run.state._hour_files
    run.state._hour_files = lambda: [t for t in orig() if t[0][:10] in want and t[0] < END_EXCLUSIVE]
    sets = []
    T = (start // R.HOUR_MS) * R.HOUR_MS + R.T_D_OFFSET_MS
    step = R.HOUR_MS // 2 if half_hour else R.HOUR_MS
    while T < end:
        if T >= start:
            run.poll_to(T)                      # rows up to T - 1 ms only
            run.sim_now = max(run.sim_now, T)
            run.pending = None
            run.screen(T)
            if run.pending is not None:
                cards = [run.pending["cards"][i] for i in run.pending["scr_ids"]]
                assert 3 <= len(cards) <= RK.N_MAX and all(c["as_of_ms"] == T for c in cards)
                sets.append((T, cards))
        T += step
    return sets, {k: run.counters.get(k, 0) for k in SCREEN_COUNTERS}


def dump(days: list[str], out_dir: Path, tape: Path, scratch: Path, *, head: str, start_ms: int | None = None, end_ms: int | None = None,
         half_hour: bool = False) -> dict[str, Any]:
    out_dir = check_out(out_dir)
    check_empty(out_dir)
    sets, counters = collect(days, scratch, tape, start_ms=start_ms, end_ms=end_ms, half_hour=half_hour)
    calls = []
    for t_d, cards in sets:
        for tag, rr in requests_for_set(t_d, cards):
            calls.append({"set_id": t_d, "shuffle": tag, "card_ids": list(rr.shown_ids), "system": rr.request.system, "user": rr.request.user_text,
                          "request_sha256": rr.request.request_sha256()})
    enforce_caps(len(sets), len(calls), days, half_hour)          # before anything is written
    (out_dir / "prompts").mkdir(parents=True, exist_ok=True)
    files = []
    for n, i in enumerate(range(0, len(calls), CALLS_PER_FILE)):
        name = f"batch-{n:02d}.jsonl"
        chunk = calls[i:i + CALLS_PER_FILE]
        body = "".join(json.dumps({"chunk_id": name[:-6], **{k: c[k] for k in ("set_id", "shuffle", "card_ids", "system", "user")}}, sort_keys=True, separators=(",", ":")) + "\n"
                       for c in chunk).encode()
        (out_dir / "prompts" / name).write_bytes(body)
        files.append({"file": f"prompts/{name}", "calls": len(chunk), "sha256": hashlib.sha256(body).hexdigest()})
    per_day: dict[str, int] = {d: 0 for d in days}
    n_hist: dict[str, int] = {}
    for t_d, cards in sets:
        per_day[utc_day(t_d)] = per_day.get(utc_day(t_d), 0) + 1
        n_hist[str(len(cards))] = n_hist.get(str(len(cards)), 0) + 1
    rr0 = RK.load_rank_playbook(), RK.load_rank_template()
    manifest = {"hv7_dump_requests": "manifest", "days": list(days), "sets": len(sets), "calls": len(calls), "files": files, "sets_per_day": per_day,
                "n_histogram": dict(sorted(n_hist.items())), "shuffles": list(SHUFFLES), "calls_per_file": CALLS_PER_FILE, "playbook_sha256": rr0[0].sha256,
                "template_sha256": rr0[1].sha256, "request_sha256": [c["request_sha256"] for c in calls], "api_calls": 0, "outcome_free": True, "git_head": head, "half_hour": half_hour,
                "screen_counters": counters}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n")
    return {k: manifest[k] for k in ("sets", "calls", "sets_per_day", "n_histogram")} | {"files": len(files), "hv7_dump_requests": "done", "api_calls": 0, "git_head": head, "half_hour": half_hour, "screen_counters": counters}


def run_dump(days: list[str], drill_id: str, out: str, *, tape_dir: str | None = None, scratch: Path | None = None, half_hour: bool = False,
             head: str | None = None) -> dict[str, Any]:
    tape_dir = REAL_TAPE if tape_dir is None else tape_dir
    if tape_dir != REAL_TAPE:
        raise Refused("tape_dir_not_allowed")
    if not ID_RE.fullmatch(drill_id):
        raise Refused("drill_id")
    out_dir = check_out(out)
    check_empty(out_dir)
    head = repo_head() if head is None else head
    scratch = Path(scratch) if scratch is not None else DRILL_ROOT / drill_id
    if not is_drill_root(scratch):
        raise Refused("not_a_drill_root")
    R.check_path(scratch / "x", "w")
    scratch.mkdir(parents=True, exist_ok=True)
    if hour_scan(Path(tape_dir), days)["hours_present"] == 0:
        raise Refused("no_hour_files_for_the_days")
    return dump(days, out_dir, Path(tape_dir), scratch, head=head, half_hour=half_hour)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dump the exact HV7 rank requests (A and B shuffles) for 2026-10-21..23. Counts only; 0 API.")
    ap.add_argument("--days", default="2026-10-21,2026-10-22,2026-10-23")
    ap.add_argument("--drill-id", required=True, help="scratch id under the HV7 drill root (the runner's log needs a dir)")
    ap.add_argument("--half-hour", action="store_true", help="add the :32 instants (<= 144 sets / 288 calls); only if the hourly run gave fewer than 30 sets; replaces that run")
    ap.add_argument("--out", required=True, help="must be inside /data2/mal/hv7-rehearsal/")
    a = ap.parse_args(argv)
    try:
        res = run_dump(parse_days(a.days), a.drill_id, a.out, half_hour=a.half_hour)
    except Refused as e:
        print(json.dumps({"hv7_dump_requests": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only
        print(json.dumps({"hv7_dump_requests": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    print(json.dumps(res, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
