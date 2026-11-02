"""One-command registration of `h5v1q50-boostfloor-s1t` in ITS OWN arena root, with its own A3 books files and a write-once registration record (DEC-031 Amendment 24 items 2, 5, 7 and 9, which carry
Amendment 22 item 9). PAPER ONLY. No outcome is read.

    HOME=/home/claude/s1thome python -m tools.s1t_register --arena-root /home/claude/s1thome/data/arena --clock 2026-11-07 [--apply] --preclock-notebook-ref REF ... (see --help)

Modelled on `tools/q50s1_register.py` (s1's tool; imported for its pure helpers, NOT edited). s1t's root is S1T_ROOT = /home/claude/s1thome/data/arena on mal-research-0 (the manager creates it). The pinned scorer
(`tools/arena_h5_score._arena_root`) and S*''s shadow accept only realpath(/data/mal/arena) and realpath($HOME/data/arena), so EVERY s1t process that checks a root runs with HOME=/home/claude/s1thome
(this tool, the scorer, the shadow), and then $HOME/data/arena IS s1t's root. The smoke's root (HOME=/home/claude/s1tsmoke) is sealed and never registered; s1's root, Q50's real root (the account's
real home + /data/arena) and HV4's /data/mal/arena are refused.

The values are the compiled rows of `tools/arena_h5_s1t_instant.py`: `--clock 2026-11-07` (ROW_1107; P0 = Q50's #1413 value, reused) or `--clock 2026-11-10` (ROW_1110, the first slip; its P0 is a tip count made
before the registration and is a REQUIRED `--p0-per-day` / `--p0-ref`; for the 11-07 row those two flags are refused). There is no sha, clock, end, cap or stake flag. The five confirmations the shadow start script
needs (Amendment 24 items 4, 6 and 7; run-commands section 4) are REQUIRED arguments, format-checked and recorded, never judged here: the pre-clock notebook entry, the smoke's PASS job id, the equivalence job id,
the feed-bar records line, the EXP-024 s1t note reference, the A3 loop job id and the A3 guard commit; and the tip hour-health thresholds and pins files (QP on #1202), whose sha256 and the tool's are pinned in the record.

Default is a DRY RUN: every check runs and nothing is written. With --apply, under the registry lock (`bots.json.lock`):
(1) refuse inside :04 to :25 of the 07, 13 and 19 UTC hours and inside 12:10 to 12:25Z (the A3 loop and dead-man windows), and off mal-research-0;
(2) refuse if --arena-root resolves (realpath, symlinks included) to Q50's, s1's, HV4's or the smoke's root, is not an existing directory, or its `bots.json` holds any bot other than s1t;
(3) the 3-key and 2-key books texts must pass tools.a3_halt.validate_books against the registry WITH s1t added;
(4) the pinned tools of the smoke and watch (judge, watcher, probe) must have the registered sha256 in this checkout;
(5) the s1t line is built from the compiled row (tools.arena_h5_score_s1t.add_line: the row's registration window, before the clock, window_end_ms pinned) with the key-free host-only side-feed endpoint;
(6) both books files are published write-once (atomic hard link of an fsynced 0444 temp file; a file already there with other bytes is refused), THEN the s1t line is written atomically (the commit point: a
    line implies both books files), THEN the registration record `s1t-registration.json` is published write-once. A rerun after a crash between the last two steps completes the record.
A rerun on a finished registration with the same arguments is an "already_done" no-op that writes nothing. A different record, books file or line is a manual state, refused, not repaired.

main() prints the JSON report on its first line, then the books texts and the registration record verbatim with their sha256 for the notebook copy. It starts no process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

from tools import a3_halt
from tools import arena_h5_s1t_instant as ti
from tools import arena_h5_score as sc
from tools import arena_h5_score_s1t as w
from tools import q50s1_register as R
from tools import tip_hour_health as th
from tools.arena_score import Refused

S1T_ID = ti.S1T_ID
S1T_HOME = "/home/claude/s1thome"  # every s1t process runs with HOME=S1T_HOME, so $HOME/data/arena is s1t's root and the pinned root checks are unchanged
S1T_ROOT = S1T_HOME + "/data/arena"
SMOKE_ROOT = "/home/claude/s1tsmoke/data/arena"  # the smoke's scratch root (HOME=/home/claude/s1tsmoke): sealed, never registered
S1_ROOT = R.S1_ROOT
HV4_ROOT = R.HV4_ROOT
HOST = "mal-research-0"
CLOCKS = {"2026-11-07": ti.ROW_1107, "2026-11-10": ti.ROW_1110}
A3_BOOKS_NAME, A3_BOOKS_READ_NAME, RECORD_NAME = "a3-books.json", "a3-books-read.json", "s1t-registration.json"
# Amendment 22 item 9 with the s1t id. The texts are fixed bytes: json.dumps of the dicts below plus a newline (the tests recompute both the texts and the sha256 pins).
A3_BOOKS_TEXT = json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": [S1T_ID], "fees_check_bot_ids": [S1T_ID]}) + "\n"
A3_BOOKS_READ_TEXT = json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": [S1T_ID]}) + "\n"
A3_BOOKS_SHA256 = "7bc537b860c434c09fe16ceee01c58a74ec2aab49d39a225dbc0bc488e6605e5"
A3_BOOKS_READ_SHA256 = "e5062afb6fb3ef19058b36b8c6e04397e96a98526e3f77865cefede381219f6e"
# the smoke's and watch's tools, registered by #1193 (run-commands section 0 item 6 and section 5): sha256 of the files in this checkout
PINNED_TOOLS = {"tools/s1t_smoke_judge.py": "a91c29bf5ff70180cdbda8cad818569a45a14248608ae64b995845844d00689d",
                "tools/s1t_arena_feed_watch.py": "b9941f82c6d17dd567978317e9ecddb50f2cce84df35904d85fe3e580131becb",
                "tools/s1t_side_feed_probe.py": "68f0c51330854a4c4652d1b67ddf485a3d75aeb8a563ee20d1408995c0a93bf2"}
HOUR_HEALTH_TOOL = "tools/tip_hour_health.py"
HOUR_HEALTH_PROGRAMS = ("pumpswap", "pumpfun")  # Amendment 24 item 7: hour health for programs pumpswap and pumpfun
T_MAX_S = 333.0  # Amendment 24 item 7: T_max = 333 s (330 s exit + 0.55 s + 1.9 s)
REF_RE = R.NOTEBOOK_REF_RE
JOB_RE = re.compile(r"[0-9]{1,9}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
REF_ARGS = (("preclock_notebook_ref", REF_RE), ("smoke_pass_job", JOB_RE), ("equiv_job", JOB_RE), ("feedbar_note", REF_RE), ("exp024_note_ref", REF_RE), ("a3_loop_job", JOB_RE),
            ("a3_guard_commit", COMMIT_RE))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def check_root(arena_root: str | os.PathLike) -> Path:
    """The resolved s1t root. Refused: Q50's real root, s1's, HV4's and the smoke's (all by realpath, so a symlink is caught too) and anything that is not an existing directory. Writes nothing."""
    real = os.path.realpath(arena_root)
    for name, other in (("Q50's root", R.q50_root()), ("s1's root", S1_ROOT), ("HV4's root", HV4_ROOT), ("the sealed smoke's scratch root", SMOKE_ROOT)):
        if real == os.path.realpath(other):
            raise Refused(f"--arena-root {os.fspath(arena_root)!r} resolves to {name} {real}: s1t has its own root {S1T_ROOT} (DEC-031 Amendment 24 item 7; Amendment 22 item 9)")
    if not os.path.isdir(real):
        raise Refused(f"--arena-root {os.fspath(arena_root)!r} is not an existing directory (the manager creates s1t's root; this tool does not)")
    return Path(real)


def check_refs(refs: Mapping[str, str | None]) -> dict[str, str]:
    out = {}
    for k, rx in REF_ARGS:
        v = refs.get(k)
        if not isinstance(v, str) or not rx.fullmatch(v):
            raise Refused(f"--{k.replace('_', '-')} is required and must match {rx.pattern!r} (format check only: it is recorded, not judged)")
        out[k] = v
    return out


def check_tools(repo_root: Path) -> dict[str, str]:
    for rel, want in PINNED_TOOLS.items():
        try:
            got = _sha(Path(repo_root, rel).read_bytes())
        except OSError as e:
            raise Refused(f"cannot read the pinned tool {rel}: {e}") from None
        if got != want:
            raise Refused(f"{rel} sha256 {got} is not the registered {want}: register from the checkout whose tools are the registered ones")
    return dict(PINNED_TOOLS)


def check_hour_health(repo_root: Path, thresholds_path: str | os.PathLike | None, pins_path: str | os.PathLike | None) -> dict:
    """The tip hour-health pins of the registration record (QP on #1202; Amendment 24 item 7, Amendment 23 item 5): the tool's sha256 in this checkout, the thresholds file (validated by the tool's
    own loader) and the pins file (three 64-hex sha256), both with their sha256, the two programs and T_max. Both files are REQUIRED. Reads only these config files."""
    if thresholds_path is None or pins_path is None:
        raise Refused("--hour-health-thresholds and --hour-health-pins are required (the registration record pins tools/tip_hour_health.py, its thresholds and pins)")
    try:
        tool = Path(repo_root, HOUR_HEALTH_TOOL).read_bytes()
        thr_b, pins_b = Path(thresholds_path).read_bytes(), Path(pins_path).read_bytes()
        thr = th.load_thresholds(thr_b)
        pins, src = th.load_pins(os.fspath(pins_path), {})
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused(f"hour-health pins: {type(e).__name__}: {e}") from None
    if not thr or pins is None or src != "file":
        raise Refused("hour-health thresholds must be a non-empty object and the pins a file of the three sha256")
    return {"tool": HOUR_HEALTH_TOOL, "sha256": _sha(tool), "schema": th.SCHEMA, "programs": list(HOUR_HEALTH_PROGRAMS), "t_max_s": T_MAX_S, "thresholds": thr,
            "thresholds_sha256": _sha(thr_b), "pins": pins, "pins_sha256": _sha(pins_b)}


def p0_for(row: Mapping, p0_per_day: float | None, p0_ref: str | None) -> tuple[float, str]:
    """The 11-07 row's P0 is the compiled #1413 value and no P0 argument is accepted; the slip row needs a tip count and its notebook reference."""
    if row["p0_per_day"] is not None:
        if p0_per_day is not None or p0_ref is not None:
            raise Refused("the 11-07 row's P0 is the compiled #1413 value (Amendment 24 item 5): --p0-per-day and --p0-ref are refused")
        return float(row["p0_per_day"]), str(row["p0_ref"])
    if p0_per_day is None or p0_ref is None:
        raise Refused("the slip row needs --p0-per-day and --p0-ref: its P0 is a tip count made before the registration (Amendment 24 item 5)")
    if isinstance(p0_per_day, bool) or not isinstance(p0_per_day, (int, float)) or not math.isfinite(p0_per_day) or p0_per_day <= 0:
        raise Refused("--p0-per-day must be a finite number above 0")
    if not REF_RE.fullmatch(p0_ref):
        raise Refused("--p0-ref must be the notebook entry id of the P0 count")
    return float(p0_per_day), f"notebook {p0_ref}"


def build_record(row: Mapping, *, reg_ms: int, refs: Mapping[str, str], p0_per_day: float, p0_ref: str, host: str, tools: Mapping[str, str], hour_health: Mapping) -> dict:
    """The registration record (Amendment 24: bot_id, clock, S_cap, H, P0 reference, tag commit, pins). Public values, hashes and references only."""
    return {"bot_id": S1T_ID, "registered_at_ms": int(reg_ms), "host": host, "clock_start_ms": int(row["clock_start_ms"]), "screen_cap_ms": int(row["screen_cap_ms"]),
            "window_end_ms": int(row["window_end_ms"]), "confirm_end_ms": int(row["confirm_end_ms"]), "h_days": int(row["h_days"]), "screen_n": int(row["screen_n"]),
            "confirm_n": int(row["confirm_n"]), "p0_per_day": p0_per_day, "p0_ref": p0_ref, "p0_rule": row["p0_rule"],
            "frozen_sha": row["frozen_sha"], "tag": row["tag"], "tag_object": row["tag_object"], "s_star": row["s_star"], "shadow_blob": row["shadow_blob"],
            "wrapper_blob": row["wrapper_blob"], "rule": row["rule"], "rule_sha256": row["rule_sha256"], "q_star_sol": row["q_star_sol"], "exit_rule": row["exit_rule"],
            "s0_margin_ms": int(row["s0_margin_ms"]), "end_only_ms": int(row["end_only_ms"]), "tip_input": row["tip_input"], "fresh_forward": row["fresh_forward"],
            "syn_sockets": int(row["syn_sockets"]), "side_feed_ws": row["side_feed_ws"], "endpoints": [row["side_feed_host"]],
            "a3_books_sha256": A3_BOOKS_SHA256, "a3_books_read_sha256": A3_BOOKS_READ_SHA256, "pinned_tools": dict(tools), "tip_hour_health": dict(hour_health), "refs": dict(refs),
            "instant_file": ti.INSTANT_FILE, "decision": "DEC-031 Amendment 24 items 2, 5, 7 and 9; amends Amendment 17; not push-family; N + 1 at the clock (item 10)",
            "note": "paper only; no book has cleared the promotion gate; nothing here is a profit claim"}


def record_text(rec: Mapping) -> str:
    return json.dumps(rec, sort_keys=True, indent=1) + "\n"


def run(arena_root: str | os.PathLike, row: Mapping, *, now_ms: int, apply: bool, refs: Mapping[str, str | None], p0_per_day: float | None = None, p0_ref: str | None = None,
        repo_root: str | os.PathLike | None = None, host: str | None = None, hh_thresholds: str | os.PathLike | None = None, hh_pins: str | os.PathLike | None = None) -> dict:
    """Returns the report dict. Raises Refused / ValueError / a3_halt.Invalid on any doubt before anything is written."""
    for text, pin in ((A3_BOOKS_TEXT, A3_BOOKS_SHA256), (A3_BOOKS_READ_TEXT, A3_BOOKS_READ_SHA256)):
        if _sha(text.encode()) != pin:
            raise Refused("a books text does not match its sha256 pin: the module was edited")
    host = host or os.uname().nodename
    if apply and host != HOST:
        raise Refused(f"--apply runs on {HOST} (this is {host}): the s1t book and its registry live there")
    root = check_root(arena_root)  # structural refusals first: they do not depend on the clock
    if row is not ti.ROWS.get(row["clock_start_ms"]):
        raise Refused("the row is not a compiled s1t row (tools/arena_h5_s1t_instant.py)")
    refs = check_refs(refs)
    p0, p0_text = p0_for(row, p0_per_day, p0_ref)
    repo = Path(repo_root) if repo_root else Path(__file__).resolve().parent.parent
    tools = check_tools(repo)
    hour_health = check_hour_health(repo, hh_thresholds, hh_pins)
    forbidden = R.forbidden_minute(now_ms)
    if forbidden and apply:
        raise Refused(f"refused at {now_ms}: {R.FORBIDDEN_NOTE}")
    books, read_books, record_path, registry = root / A3_BOOKS_NAME, root / A3_BOOKS_READ_NAME, root / RECORD_NAME, root / "bots.json"
    endpoints = R.check_endpoints([row["side_feed_host"]])
    sockets = int(row["syn_sockets"])

    def go():
        bots = sc.load_registry(registry)
        extra = sorted(set(bots) - {S1T_ID})
        if extra:
            raise Refused(f"{registry} holds {extra}: s1t's own root holds only the s1t line; this tool does not touch another book's registry")
        state = {books: R._file_state(books, A3_BOOKS_TEXT), read_books: R._file_state(read_books, A3_BOOKS_READ_TEXT)}  # refuses a different file before any write
        rep = {"arena_root": os.fspath(root), "clock_start_ms": int(row["clock_start_ms"]), "screen_cap_ms": int(row["screen_cap_ms"]), "a3_books": os.fspath(books),
               "a3_books_sha256": A3_BOOKS_SHA256, "a3_books_read": os.fspath(read_books), "a3_books_read_sha256": A3_BOOKS_READ_SHA256, "endpoints": endpoints,
               "record": os.fspath(record_path)}
        if S1T_ID in bots:
            missing = sorted(os.fspath(p) for p, s in state.items() if s == "missing")
            if missing:
                raise Refused(f"the s1t line is registered but {missing} is missing: a manual state, not mine to repair")
            old = bots[S1T_ID]
            if int(old["clock_start_ms"]) != int(row["clock_start_ms"]):
                raise Refused(f"{S1T_ID} is registered with clock {old['clock_start_ms']}, not {row['clock_start_ms']}: a change is a new bot_id")
            w.add_line(dict(bots), row, sockets=sockets, endpoints=endpoints, host=old["host"], reg_ms=int(old["registered_at_ms"]))  # CHANGE_KEYS check against the compiled row
            text = record_text(build_record(row, reg_ms=int(old["registered_at_ms"]), refs=refs, p0_per_day=p0, p0_ref=p0_text, host=old["host"], tools=tools, hour_health=hour_health))
            st = R._file_state(record_path, text)
            rep.update(status="already_done" if st == "ok" else ("completed_record" if apply else "dry_run_would_complete_record"), registered_at_ms=int(old["registered_at_ms"]),
                       record_sha256=_sha(text.encode()))
            if st == "missing" and apply:
                R._publish_once(record_path, text)
            rep["record_text"] = text
            return rep
        if record_path.exists():  # a record without a line is a manual state
            raise Refused(f"{record_path} exists without an s1t line: a manual state, not mine to repair")
        window_ok = int(row["register_not_before_ms"]) <= now_ms <= int(row["register_not_after_ms"])
        trial: dict[str, dict] = {}
        if window_ok or apply:  # the window, clock and change checks, in memory only; a dry run outside the window still runs every other check and reports window_ok false
            entry = w.add_line(trial, row, sockets=sockets, endpoints=endpoints, host=host, reg_ms=now_ms)
        else:
            entry = trial[S1T_ID] = w.s1t_line(row, sockets=sockets, endpoints=endpoints, host=host, reg_ms=now_ms)
        legacy, new = a3_halt.validate_books(json.loads(A3_BOOKS_TEXT), sorted(trial))  # unregistered / uncovered / duplicate / both-lists refusals
        if a3_halt.validate_books(json.loads(A3_BOOKS_READ_TEXT), sorted(trial)) != (legacy, new):
            raise Refused("the read books file and the A3 books file list different books")
        text = record_text(build_record(row, reg_ms=now_ms, refs=refs, p0_per_day=p0, p0_ref=p0_text, host=host, tools=tools, hour_health=hour_health))
        rep.update(status="applied" if apply else "dry_run", registered_at_ms=now_ms, window_end_ms=entry["window_end_ms_pin"], frozen_sha=entry["frozen_sha"], legacy=legacy,
                   new_rule=new, window_ok=window_ok, window=[int(row["register_not_before_ms"]), int(row["register_not_after_ms"])], forbidden_minute=forbidden,
                   record_sha256=_sha(text.encode()), record_text=text)
        if not apply:
            return rep
        for p, t in ((books, A3_BOOKS_TEXT), (read_books, A3_BOOKS_READ_TEXT)):
            if state[p] == "missing":
                R._publish_once(p, t)
        for p, t in ((books, A3_BOOKS_TEXT), (read_books, A3_BOOKS_READ_TEXT)):
            if _sha(p.read_bytes()) != _sha(t.encode()):
                raise Refused(f"{p} does not match its pin after the write: STOP and tell the manager")
        sc._atomic_write_json(registry, {"bots": trial})  # the s1t line: the commit point
        R._publish_once(record_path, text)
        return rep

    return sc._with_registry_lock(registry, go)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s1t_register", description=__doc__.split("\n")[0])
    ap.add_argument("--arena-root", required=True)
    ap.add_argument("--clock", default="2026-11-07", choices=sorted(CLOCKS), help="the pre-committed clock (Amendment 24 item 9): 2026-11-07, or the first slip 2026-11-10")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--now-ms", type=int, default=None, help="dry run / fixtures only; refused with --apply (a real run uses the wall clock)")
    ap.add_argument("--preclock-notebook-ref", required=True, help="the MiScusi notebook entry that lists the pre-clock items (format checked only)")
    ap.add_argument("--smoke-pass-job", required=True, help="the smoke launcher's job id with judge PASS and class unknown: no (format checked only)")
    ap.add_argument("--equiv-job", required=True, help="the job id of the K1 equivalence EQUAL (Amendment 24 item 3)")
    ap.add_argument("--feedbar-note", required=True, help="the records line of Amendment 23 item 6 / Amendment 24 item 7 (notebook entry id)")
    ap.add_argument("--exp024-note-ref", required=True, help="the EXP-024 s1t note (Amendment 24 item 4): notebook entry id or PR reference token")
    ap.add_argument("--a3-loop-job", required=True, help="the A3 loop job id (scripts/ops/s1t-a3-loop.sh)")
    ap.add_argument("--a3-guard-commit", required=True, help="the 40-hex commit of the A3 guard tag (a3-guard-pin-s1t)")
    ap.add_argument("--hour-health-thresholds", required=True, help="the thresholds JSON of tools/tip_hour_health.py (Amendment 23 item 5), pinned by sha256 in the record")
    ap.add_argument("--hour-health-pins", required=True, help="the pins JSON (adapter, reader, writer sha256) of tools/tip_hour_health.py, pinned by sha256 in the record")
    ap.add_argument("--p0-per-day", type=float, default=None, help="the slip row only: the tip P0 per day")
    ap.add_argument("--p0-ref", default=None, help="the slip row only: the notebook entry of the P0 count")
    ap.add_argument("--host", default=None)
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if a.apply and a.now_ms is not None:
        print("REFUSED: --now-ms is not accepted with --apply (the registration instant is the wall clock)", file=sys.stderr)
        return 2
    now = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    refs = {k: getattr(a, k) for k, _ in REF_ARGS}
    try:
        rep = run(sc._arena_root(a.arena_root), CLOCKS[a.clock], now_ms=now, apply=a.apply, refs=refs, p0_per_day=a.p0_per_day, p0_ref=a.p0_ref, host=a.host,
                  hh_thresholds=a.hour_health_thresholds, hh_pins=a.hour_health_pins)
    except (Refused, ValueError, a3_halt.Invalid) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    text = rep.pop("record_text")
    print(json.dumps(rep, sort_keys=True))
    for name, sha, t in ((A3_BOOKS_NAME, A3_BOOKS_SHA256, A3_BOOKS_TEXT), (A3_BOOKS_READ_NAME, A3_BOOKS_READ_SHA256, A3_BOOKS_READ_TEXT), (RECORD_NAME, rep["record_sha256"], text)):
        print(f"# {name} sha256 {sha}, verbatim:")
        print(t, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
