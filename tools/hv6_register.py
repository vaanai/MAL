"""HV6 registration wrapper (DESIGN section 8.3 route (a), conditions a1-a7; QP ruling Addendum 1).

Calls the UNMODIFIED tools.arena_paper.register_bot for exactly HV6's five hard-coded bot ids and nothing else.
- a1: ids are hard-coded, all start with `hv6.`; any other id is refused.
- a2: stdout gets only the dict register_bot returns for an HV6 id. On any exception stderr gets only the exception class name and the HV6 bot id,
  never the message (a JSON parse error on a foreign line could carry that line); exit non-zero. No other line, count or id of the registry is shown.
- a4: manager-run once, after the smoke passes at the final sha. The real registry needs BOTH --live-registry and --confirm with the exact string.
  Without --live-registry, any path under /data/mal/arena is refused (symlinks resolved) and --registry is required.
- a7: re-running an identical call is a no-op in register_bot; a changed value is refused there (new bot_id, never an edit).
Paper only. Never prints anything but HV6's own entries.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from tools import arena_paper

HV6_BOTS = ("hv6.0-L", "hv6.0-L-nr", "hv6.0-F", "hv6.0-P", "hv6.0-X")
# DESIGN: X is a rule book with no LLM; the other four arms call claude-haiku-5-5 (P's nightly reviewer included).
HV6_MODELS = {b: (["claude-haiku-5-5"] if b != "hv6.0-X" else []) for b in HV6_BOTS}
DECIDING_STAKE = 0.25
CONFIRM = "REGISTER-HV6-FIVE-BOOKS"
ARENA_ROOT = Path(arena_paper.ARENA_ROOT)


class Refused(Exception):
    """Own argument refusal; carries no text from outside this module."""


def _under_arena(path: str | os.PathLike) -> bool:
    rp = Path(os.path.realpath(path))
    root = Path(os.path.realpath(ARENA_ROOT))
    return rp == root or root in rp.parents


def register_all(frozen_sha: str, clock_start_ms: int, path: str | os.PathLike, *, bot_ids=HV6_BOTS, now_ms: int | None = None,
                 verify_sha: bool = True, repo: str | os.PathLike | None = None, out=None) -> dict[str, dict[str, Any]]:
    """Register the HV6 ids; return and print only the dicts register_bot returned. Raises the underlying exception unchanged (main() masks it)."""
    out = out if out is not None else sys.stdout
    res: dict[str, dict[str, Any]] = {}
    for bid in bot_ids:
        if bid not in HV6_BOTS or not bid.startswith("hv6."):
            raise Refused()
        kw: dict[str, Any] = {}
        if now_ms is not None:
            kw["now_ms"] = now_ms
        if not verify_sha:
            kw["verify_sha"] = False
        if repo is not None:
            kw["repo"] = repo
        res[bid] = arena_paper.register_bot(bid, frozen_sha, clock_start_ms, path, deciding_stake=DECIDING_STAKE,
                                            model_ids=HV6_MODELS[bid], **kw)
        print(f"{bid} {sorted(res[bid].items())!r}", file=out)
    return res


def main(argv: list[str] | None = None, *, out=None, err=None, _now_ms: int | None = None, _verify_sha: bool = True) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    ap = argparse.ArgumentParser(prog="hv6_register")
    ap.add_argument("--sha", required=True)
    ap.add_argument("--clock-start-ms", required=True, type=int)
    ap.add_argument("--registry", help="registry file path (required unless --live-registry)")
    ap.add_argument("--live-registry", action="store_true", help="use the real /data/mal/arena/bots.json (manager, a4)")
    ap.add_argument("--confirm", default="", help=f"must equal {CONFIRM} with --live-registry")
    a = ap.parse_args(argv)
    if a.live_registry:
        if a.confirm != CONFIRM or a.registry:
            print("Refused", file=err)
            return 2
        path: str = arena_paper.REGISTRY_PATH
    else:
        if not a.registry or _under_arena(a.registry):
            print("Refused", file=err)
            return 2
        path = a.registry
    cur = None
    try:
        for bid in HV6_BOTS:
            cur = bid
            register_all(a.sha, a.clock_start_ms, path, bot_ids=(bid,), now_ms=_now_ms, verify_sha=_verify_sha, out=out)
    except Exception as e:  # a2: class name and HV6 id only, never the message
        print(f"{type(e).__name__} {cur}", file=err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
