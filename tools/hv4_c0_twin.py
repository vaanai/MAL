"""hv4_c0_twin: the pinned twin producer of the HV4 live C0 adapter. PAPER ONLY: no RPC, no key, no wallet.

    python -m tools.hv4_c0_twin --trips TRIPS.jsonl --ledger-dir DIR --bot-id hv4-...-F|X --out TWINS.jsonl [--now-ms MS]

A thin wrapper over `tools.arena_twin_writer` (nofail leg, 1.9 s, the trip's own stake S, rent 0, E1 excluded deciding, fees as sent from the trips file). It adds:
  * the bot must be an allowed C0 arm (hv4_live_c0.arm_refusal);
  * the writer's file must hash to hv4_live_c0.TWIN_WRITER_SHA256;
  * the config pin is hv4_live_c0.CONFIG_PIN_SHA256 (every ledger row's stack block must hash to it, else the writer refuses the whole run);
  * the ledger directory must not resolve under /data/mal/arena. There is no read-record gate yet (a later PR), and R2.1 of QP-RULING-1025 forbids reading
    HV4 rows before a recorded PASS, so only a synthetic ledger can be used until then.
Exit codes are the writer's (0 written, 2 refused / unusable input). Nothing here says a book makes money.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Sequence

from tools import arena_twin_writer as W
from tools import hv4_live_c0 as c0


def refusal(bot_id: str, ledger_dir: str) -> str | None:
    why = c0.arm_refusal(bot_id)
    if why:
        return why
    if hashlib.sha256(Path(W.__file__).read_bytes()).hexdigest() != c0.TWIN_WRITER_SHA256:
        return "twin_writer_sha_mismatch"
    real = os.path.realpath(ledger_dir)
    if c0._under_sealed(real) or c0._under_sealed(os.path.abspath(ledger_dir)):
        return "ledger_dir_sealed_no_read_record_gate"
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
    for a in ("trips", "ledger-dir", "bot-id", "out", "run-record"):
        ap.add_argument(f"--{a}", action=_Once, required=a not in ("run-record",))
    for a in ("join-tol-ms", "now-ms"):
        ap.add_argument(f"--{a}", action=_Once, type=int)
    return ap  # the writer's H5-only options and every pinned option (--config-hash, --buy-send, --sell-send) are unknown here: argparse refuses them


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ONCE; the refusal runs on the parsed values and exactly those values are forwarded to the writer (no second parse of the raw argv)."""
    try:
        a = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except (SystemExit, argparse.ArgumentError) as exc:
        print(f"hv4_c0_twin: REFUSED unusable arguments ({exc})", file=sys.stderr)
        return 2
    why = refusal(a.bot_id, a.ledger_dir)
    if why:
        print(f"hv4_c0_twin: REFUSED {why}", file=sys.stderr)
        return 2
    fwd = ["--trips", a.trips, "--ledger-dir", a.ledger_dir, "--bot-id", a.bot_id, "--out", a.out,
           "--config-hash", c0.CONFIG_PIN_SHA256, "--buy-send", str(c0.PRIORITY_PIN["buy"] + c0.BASE_FEE_LAMPORTS),
           "--sell-send", str(c0.PRIORITY_PIN["sell"] + c0.BASE_FEE_LAMPORTS)]
    for name, v in (("--run-record", a.run_record), ("--join-tol-ms", a.join_tol_ms), ("--now-ms", a.now_ms)):
        if v is not None:
            fwd += [name, str(v)]
    return W.main(fwd)


if __name__ == "__main__":
    sys.exit(main())
