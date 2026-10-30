"""HV7 registration wrapper, modelled on tools/hv6_register.py. Calls the UNMODIFIED tools.arena_paper.register_bot for exactly HV7's four bot ids.

Pins (revised 2026-10-30, after real-model smoke #1779 FAILED and HV7 slipped past the 11-01 clock): clock in {2026-11-03T00Z .. 2026-11-08T00Z} (one per
UTC day, six rolling clocks), H == clock + 14 days (H_DAYS; DEC-031 Am.4 item 2.2: max(14, ceil(150 / rate)) = 14 at P0's 23.33 periods/day; DESIGN section 6:
H follows that formula once the owner extends the API horizon past 2026-11-11, which the owner did on 2026-10-28, and the registration must quote that line
before the clock), stake 0.25, a 40-hex sha. The old pins (11-01/11-02, H = 2026-11-11T00Z) are gone: both clocks have passed or are unreachable. H is stored in the registry as one `hv7_halt` line per book
(load_registry ignores the kind; hv7_runner.main reads it back and refuses a --entries-halt-ms or --clock-start-ms that differs). (All checked before anything is written): the four ids hv7.0-L, -R, -S, -U (hard-coded, every id starts with `hv7.`); the frozen sha; the clock
start; H (the entries-halt instant); the caps (HV7_DAY_CAP_USD and the K1 sum); deciding stake 0.25; models (L: claude-haiku-5-5 via hv6_agents.MODEL_ID,
R/S/U: none).
DRY RUN BY DEFAULT: without --execute it prints the plan and the exact --confirm phrase and writes nothing. With --execute the real registry
(--live-registry) needs --confirm equal to the phrase, which embeds the sha, clock and H, so a changed pin changes the phrase. Without --live-registry any path
under /data/mal/arena is refused (symlinks resolved) and --registry is required. A re-run with identical values is a no-op in register_bot; a changed
value is refused there (a new bot_id, never an edit). On any exception stderr gets the class name and the bot id, never the message.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from tools import arena_paper
from tools import hv6_agents as A
from tools import hv7_runner as R

HV7_BOTS = tuple(R.bot_id(a) for a in R.ARM_ORDER)           # hv7.0-L, hv7.0-R, hv7.0-S, hv7.0-U
HV7_MODELS = {b: ([A.MODEL_ID] if b.endswith("-L") else []) for b in HV7_BOTS}
DECIDING_STAKE = R.STAKE_SOL


class Refused(Exception):
    """Own argument refusal; carries no text from outside this module."""


DAY_MS = 86_400_000
CLOCKS_MS = (1793664000000, 1793750400000, 1793836800000, 1793923200000, 1794009600000, 1794096000000)   # 2026-11-03T00Z .. 2026-11-08T00Z
H_DAYS = 14                                     # DEC-031 Am.4 item 2.2 at P0's rate (see the module docstring); never shorter, never set by a flag
HALT_BY_CLOCK_MS = {c: c + H_DAYS * DAY_MS for c in CLOCKS_MS}   # 2026-11-17T00Z .. 2026-11-22T00Z


def halt_for(clock_start_ms: int) -> int:
    """The one entries-halt instant H allowed for a pinned clock (clock + 14 days). Refused for any other clock."""
    if clock_start_ms not in HALT_BY_CLOCK_MS:
        raise Refused()
    return HALT_BY_CLOCK_MS[clock_start_ms]


CONFIRM_PREFIX = "REGISTER-HV7-FOUR-BOOKS"
ARENA_ROOT = Path(arena_paper.ARENA_ROOT)


def confirm_phrase(sha: str, clock_start_ms: int, entries_halt_ms: int) -> str:
    return f"{CONFIRM_PREFIX}:{str(sha).lower()}:{int(clock_start_ms)}:{int(entries_halt_ms)}"


def pins(sha: str, clock_start_ms: int, entries_halt_ms: int) -> dict[str, Any]:
    return {"sha": str(sha).lower(), "clock_start_ms": int(clock_start_ms), "H_entries_halt_ms": int(entries_halt_ms), "bots": list(HV7_BOTS),
            "models": {b: list(m) for b, m in HV7_MODELS.items()}, "deciding_stake": DECIDING_STAKE, "hv7_day_cap_usd": R.HV7_DAY_CAP_USD,
            "k1_day_cap_usd": R.K1_DAY_CAP_USD, "cap_sum_usd": R.cap_sum_usd(), "confirm": confirm_phrase(sha, clock_start_ms, entries_halt_ms)}


def check_pins(sha: str, clock_start_ms: int, entries_halt_ms: int, *, now_ms: int | None = None) -> None:
    s = str(sha).lower()
    if len(s) != 40 or not all(c in "0123456789abcdef" for c in s):
        raise Refused()                                         # a full 40-hex sha, never an abbreviation
    if clock_start_ms not in CLOCKS_MS or entries_halt_ms != halt_for(clock_start_ms) or DECIDING_STAKE != 0.25:
        raise Refused()
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if clock_start_ms < now:
        raise Refused()                                         # a clock cannot be backdated (register_bot refuses it too)
    if R.HV7_DAY_CAP_USD != 0.30 or R.cap_sum_usd() > R.K1_DAY_CAP_USD + 1e-9 or len(HV7_BOTS) != 4:
        raise Refused()


def _under_arena(path: str | os.PathLike) -> bool:
    rp = Path(os.path.realpath(path))
    root = Path(os.path.realpath(ARENA_ROOT))
    return rp == root or root in rp.parents


def _store_halt(path: str | os.PathLike, bot_id: str, entries_halt_ms: int) -> None:
    """Append the `hv7_halt` line for one book, once. A different H already stored for the id is refused."""
    import fcntl

    p = Path(path)
    with open(p.with_suffix(p.suffix + ".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        have = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        old = [r["entries_halt_ms"] for r in have if r.get("kind") == "hv7_halt" and r.get("bot_id") == bot_id]
        if old:
            if old[-1] != entries_halt_ms:
                raise Refused()
            return
        arena_paper._append_line(p, {"kind": "hv7_halt", "bot_id": bot_id, "entries_halt_ms": int(entries_halt_ms)})


def register_all(frozen_sha: str, clock_start_ms: int, path: str | os.PathLike, *, entries_halt_ms: int | None = None, bot_ids=HV7_BOTS, now_ms: int | None = None, verify_sha: bool = True,
                 repo: str | os.PathLike | None = None, out=None) -> dict[str, dict[str, Any]]:
    out = out if out is not None else sys.stdout
    res: dict[str, dict[str, Any]] = {}
    for bid in bot_ids:
        if bid not in HV7_BOTS or not bid.startswith("hv7."):
            raise Refused()
        kw: dict[str, Any] = {}
        if now_ms is not None:
            kw["now_ms"] = now_ms
        if not verify_sha:
            kw["verify_sha"] = False
        if repo is not None:
            kw["repo"] = repo
        res[bid] = arena_paper.register_bot(bid, frozen_sha, clock_start_ms, path, deciding_stake=DECIDING_STAKE, model_ids=HV7_MODELS[bid], **kw)
        if entries_halt_ms is not None:
            _store_halt(path, bid, entries_halt_ms)
        print(f"{bid} {sorted(res[bid].items())!r}", file=out)
    return res


def main(argv: list[str] | None = None, *, out=None, err=None, _now_ms: int | None = None, _verify_sha: bool = True) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    ap = argparse.ArgumentParser(prog="hv7_register")
    ap.add_argument("--sha", required=True)
    ap.add_argument("--clock-start-ms", required=True, type=int)
    ap.add_argument("--entries-halt-ms", required=True, type=int, help="H: the instant after which no new entry is made")
    ap.add_argument("--registry", help="registry file path (required unless --live-registry)")
    ap.add_argument("--live-registry", action="store_true", help="use the real /data/mal/arena/bots.json (manager only)")
    ap.add_argument("--execute", action="store_true", help="write the registry; without it this is a dry run")
    ap.add_argument("--confirm", default="", help="must equal the phrase a dry run prints, with --live-registry --execute")
    a = ap.parse_args(argv)
    try:
        check_pins(a.sha, a.clock_start_ms, a.entries_halt_ms, now_ms=_now_ms)
    except Refused:
        print("Refused", file=err)
        return 2
    p = pins(a.sha, a.clock_start_ms, a.entries_halt_ms)
    if a.live_registry:
        if a.registry:
            print("Refused", file=err)
            return 2
        path: str = arena_paper.REGISTRY_PATH
    else:
        if not a.registry or _under_arena(a.registry):
            print("Refused", file=err)
            return 2
        path = a.registry
    if not a.execute:
        if _verify_sha:
            try:
                arena_paper.verify_sha_on_origin(a.sha)
            except Exception as e:  # noqa: BLE001
                print(f"{type(e).__name__} sha", file=err)
                return 1
        print("DRY RUN: nothing written", file=out)
        for k in ("sha", "clock_start_ms", "H_entries_halt_ms", "deciding_stake", "hv7_day_cap_usd", "k1_day_cap_usd", "cap_sum_usd", "bots", "models", "confirm"):
            print(f"{k} {p[k]!r}", file=out)
        return 0
    if a.live_registry and a.confirm != p["confirm"]:
        print("Refused", file=err)
        return 2
    cur = None
    try:
        for bid in HV7_BOTS:
            cur = bid
            register_all(a.sha, a.clock_start_ms, path, entries_halt_ms=a.entries_halt_ms, bot_ids=(bid,), now_ms=_now_ms, verify_sha=_verify_sha, out=out)
    except Exception as e:  # class name and HV7 id only, never the message
        print(f"{type(e).__name__} {cur}", file=err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
