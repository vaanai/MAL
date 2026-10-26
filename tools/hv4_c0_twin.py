"""hv4_c0_twin: the pinned twin producer of the HV4 live C0 adapter. PAPER ONLY: no RPC, no key, no wallet.

    python -m tools.hv4_c0_twin --trips TRIPS.jsonl --ledger-dir DIR --bot-id hv4-...-F|X --out TWINS.jsonl [--now-ms MS]

A thin wrapper over `tools.arena_twin_writer` (nofail leg, 1.9 s, the trip's own stake S, rent 0, E1 excluded deciding, fees as sent from the trips file). It adds:
  * the bot must be an allowed C0 arm (hv4_live_c0.arm_refusal);
  * the writer's file must hash to hv4_live_c0.TWIN_WRITER_SHA256;
  * the config pin is hv4_live_c0.CONFIG_PIN_SHA256 (every ledger row's stack block must hash to it, else the writer refuses the whole run);
  * a ledger directory that resolves under, or is an inode alias of, /data/mal/arena is read ONLY after the read-record gate passes
    (hv4_live_c0.read_record_problem on `--reads-dir`: the canonical write-once record, PASS, frozen_sha, owner/mode; QP-RULING-1025 R2.1). This replaces the
    old flat path-string refusal. Without `--reads-dir` or without the record the run is refused.
  * `--out` and `--run-record` (the writer's files; default `<out>.run.json`) are seal-checked: not a symlink, not a hard link, and neither the file nor its
    parent directory may resolve under or alias the arena.
Exit codes are the writer's (0 written, 2 refused / unusable input). Nothing here says a book makes money.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Sequence

from tools import arena_twin_writer as W
from tools import hv4_live_c0 as c0


def refusal(bot_id: str, ledger_dir: str, reads_dir: str | None = None) -> str | None:
    why = c0.arm_refusal(bot_id)
    if why:
        return why
    if hashlib.sha256(Path(W.__file__).read_bytes()).hexdigest() != c0.TWIN_WRITER_SHA256:
        return "twin_writer_sha_mismatch"
    real = os.path.realpath(ledger_dir)
    if c0._under_sealed(real) or c0._under_sealed(os.path.abspath(ledger_dir)):
        why = c0.read_record_problem(reads_dir, bot_id)[0]  # the gate decides; a missing reads dir is its own refusal
        if why:
            return f"ledger_dir_sealed:{why}"
    return None


def write_problem(path: str) -> str | None:
    """Seal check of a file the writer will CREATE or replace: not a symlink, not hard-linked, and neither it nor its parent resolves under / aliases the arena."""
    try:
        ls = os.lstat(path)
        if stat.S_ISLNK(ls.st_mode):
            return "file_is_symlink"
        if not stat.S_ISREG(ls.st_mode):
            return "file_not_regular"
        if ls.st_nlink != 1:
            return "file_hard_linked"
    except FileNotFoundError:
        pass
    except OSError:
        return "file_unreadable"
    parent = os.path.dirname(os.path.abspath(path)) or "."
    if c0._under_sealed(path) or c0._under_sealed(parent):
        return "write_path_sealed"
    return None


class _Once(argparse.Action):
    """Each option once. A repeat (in any spelling) is refused: the writer's argparse would take the LAST one, the guard must see the same value."""

    def __call__(self, parser, ns, values, option_string=None):
        if getattr(ns, "_seen", None) is None:
            ns._seen = set()
        if self.dest in ns._seen:
            parser.error(f"{option_string} given twice")
        ns._seen.add(self.dest)
        setattr(ns, self.dest, values)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hv4_c0_twin", allow_abbrev=False, exit_on_error=False)
    for a in ("trips", "ledger-dir", "bot-id", "out", "run-record", "reads-dir"):
        ap.add_argument(f"--{a}", action=_Once, required=a not in ("run-record", "reads-dir"))
    for a in ("join-tol-ms", "now-ms"):
        ap.add_argument(f"--{a}", action=_Once, type=int)
    return ap  # the writer's H5-only options and every pinned option (--config-hash, --buy-send, --sell-send) are unknown here: argparse refuses them


def file_problem(path: str, sealed_ok: bool = False) -> str | None:
    """Per-FILE check (not only the directory's realpath): a synthetic dir can hold a symlink or hard link into the arena. A missing path is left to the writer."""
    try:
        ls = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return "file_unreadable"
    if stat.S_ISLNK(ls.st_mode):
        return "file_is_symlink"
    if not stat.S_ISREG(ls.st_mode):
        return "file_not_regular"
    if ls.st_nlink != 1:
        return "file_hard_linked"
    if not sealed_ok and c0._under_sealed(os.path.realpath(path)):
        return "file_sealed_realpath"
    return None


def input_problem(trips: str, ledger_dir: str, ledger_sealed_ok: bool = False) -> str | None:
    """B2: every entry of the ledger dir (the writer opens trades-<day>.jsonl) and the trips file pass the per-file check. Then B3: every trip carries the fees SENT."""
    why = file_problem(trips)
    if why:
        return f"trips:{why}"
    try:
        names = sorted(os.listdir(ledger_dir))
    except FileNotFoundError:
        names = []
    except OSError:
        return "ledger_dir_unlistable"
    for n in names:
        why = file_problem(os.path.join(ledger_dir, n), sealed_ok=ledger_sealed_ok)  # symlink / hard-link / not-regular checks stay; the gate already passed
        if why:
            return f"ledger:{n}:{why}"
    try:
        rows = [json.loads(x) for x in Path(trips).read_text().splitlines() if x.strip()] if os.path.exists(trips) else []
    except (OSError, ValueError):
        return "trips_unreadable"
    for t in rows:
        if not isinstance(t, dict):
            return "trips_row_not_object"
        isint = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
        if not isint(t.get("buy_fee_lamports")) or (t.get("exit_landed") and not isint(t.get("sell_fee_lamports"))):
            return f"fee_fields_missing:{t.get('trip_id')}"  # never priced at the plan: an escalated trip would be under-priced
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ONCE; the refusal runs on the parsed values and exactly those values are forwarded to the writer (no second parse of the raw argv)."""
    try:
        a = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except (SystemExit, argparse.ArgumentError) as exc:
        print(f"hv4_c0_twin: REFUSED unusable arguments ({exc})", file=sys.stderr)
        return 2
    why = refusal(a.bot_id, a.ledger_dir, a.reads_dir)
    if not why:
        for w in (a.out, a.run_record or a.out + ".run.json"):
            why = why or (f"write:{w}:{x}" if (x := write_problem(w)) else None)
    if why:
        print(f"hv4_c0_twin: REFUSED {why}", file=sys.stderr)
        return 2
    sealed_ledger = c0._under_sealed(os.path.realpath(a.ledger_dir)) or c0._under_sealed(os.path.abspath(a.ledger_dir))  # only reachable past the gate
    why = input_problem(a.trips, a.ledger_dir, ledger_sealed_ok=sealed_ledger)
    if why:
        print(f"hv4_c0_twin: REFUSED {why}", file=sys.stderr)
        return 2
    # --opt=value: a value that starts with "-" can never be parsed as another option by the writer's argparse
    fwd = [f"--trips={a.trips}", f"--ledger-dir={a.ledger_dir}", f"--bot-id={a.bot_id}", f"--out={a.out}",
           f"--config-hash={c0.CONFIG_PIN_SHA256}", f"--buy-send={c0.PRIORITY_PIN['buy'] + c0.BASE_FEE_LAMPORTS}",
           f"--sell-send={c0.PRIORITY_PIN['sell'] + c0.BASE_FEE_LAMPORTS}"]
    for name, v in (("--run-record", a.run_record), ("--join-tol-ms", a.join_tol_ms), ("--now-ms", a.now_ms)):
        if v is not None:
            fwd.append(f"{name}={v}")
    return W.main(fwd)


if __name__ == "__main__":
    sys.exit(main())
