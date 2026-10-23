"""DEC-031 Amendment 8: the 160,000-lamport send stack for NEW arena books. A separate module on purpose: tools/arena_rules.py (the host, a pinned file) is not edited.

`stack_config_v2` is `arena_rules.stack_config` with `send` = 160,000 (155,000 priority + 5,000 base) and every other field identical. Version 1 (505,000, the default
everywhere) is `arena_rules.stack_config` itself. `check_ledger_send` is the resume guard: a host refuses to continue a ledger whose rows carry another send."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from tools.arena_paper import StackConfig
from tools.arena_rules import DEFAULT_FEED_DELAY_SLOTS, STACK_FIELDS, HostError, stack_config

SEND_V1 = 505_000
SEND_V2 = 160_000
STACK_FIELDS_V2: dict[str, Any] = {  # every StackConfig field except feed_delay_slots, as literals (the same spelling as arena_rules.STACK_FIELDS, send changed)
    "send": 160_000, "rent": 0, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "stakes_sol": (0.25, 0.5, 1.0), "grace_slots": 10_000, "portal_ppm": 0,
    "press_intercept": -1.4197242838379789, "strand_rule": "both", "e1_floor_per_005": 384_022, "gap_slots": 150, "gap_silence_ms": 60_000, "max_future_ms": 5_000,
}


def stack_config_v2(feed_delay_slots: int = DEFAULT_FEED_DELAY_SLOTS) -> StackConfig:
    if isinstance(feed_delay_slots, bool) or not isinstance(feed_delay_slots, int) or feed_delay_slots < 0:
        raise ValueError("feed_delay_slots must be a whole number of slots >= 0")
    return StackConfig(**{**STACK_FIELDS_V2, "feed_delay_slots": feed_delay_slots})


def stack_config_for(version: int, feed_delay_slots: int = DEFAULT_FEED_DELAY_SLOTS) -> StackConfig:
    """1 = stack_config (505,000, the default), 2 = stack_config_v2 (160,000). Anything else is refused."""
    if version == 1 and not isinstance(version, bool):
        return stack_config(feed_delay_slots)
    if version == 2 and not isinstance(version, bool):
        return stack_config_v2(feed_delay_slots)
    raise ValueError("stack version must be 1 (505,000 per send) or 2 (160,000 per send, DEC-031 Amendment 8)")


def ledger_sends(ledger_root: str | Path, bot_ids: Iterable[str]) -> dict[str, set[int]]:
    """The set of stack.send values on the rows of each bot's day files (trades-*.jsonl). A line that does not parse is skipped here (the ledger's own checks own it)."""
    out: dict[str, set[int]] = {}
    for b in bot_ids:
        seen: set[int] = set()
        for f in sorted((Path(ledger_root) / b).glob("trades-*.jsonl")):
            with open(f, "rb") as fh:
                for line in fh:
                    try:
                        st = json.loads(line).get("stack")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(st, dict) and "send" in st:
                        seen.add(int(st["send"]))
        out[b] = seen
    return out


def check_ledger_send(ledger_root: str | Path, bot_ids: Iterable[str], send: int) -> None:
    """Refuse (HostError) if any row of any bot carries a stack.send other than `send`: a restart on the wrong --stack-version must not continue a book."""
    for b, seen in ledger_sends(ledger_root, bot_ids).items():
        if seen - {int(send)}:
            raise HostError(f"{b}: the ledger has rows priced at send {sorted(seen)}, this process prices at {int(send)}: refused (wrong --stack-version?)")


def report_main(argv: list[str] | None = None) -> int:
    """The read wrapper for a covered book: `python -m tools.arena_stack_v2 --expect-send 160000 <arena_report_legs args>`. It refuses (exit 3) unless every row of
    --bot in --ledger-root carries exactly that stack.send, then runs tools.arena_report_legs.main unchanged (that file is a pin and is not edited), then prints tools.arena_flip_read (Am.8 item 7)."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--expect-send", type=int, required=True)
    ap.add_argument("--bot")
    ap.add_argument("--ledger-root")
    a, rest = ap.parse_known_args(sys.argv[1:] if argv is None else argv)
    if not a.bot or not a.ledger_root:
        print("REFUSED: --bot and --ledger-root are required", file=sys.stderr)
        return 3
    seen = ledger_sends(a.ledger_root, [a.bot])[a.bot]
    if seen - {a.expect_send}:
        print(f"REFUSED: the rows of {a.bot} carry send {sorted(seen)}, the book pins {a.expect_send}", file=sys.stderr)
        return 3
    from tools import arena_flip_read, arena_report_legs
    rc = arena_report_legs.main(["--bot", a.bot, "--ledger-root", a.ledger_root, *rest])
    # DEC-031 Am.8 item 7: every read of a covered book also prints the dust-rule flip count and SOL, and the gate re-checked with the flipped legs priced as strands.
    # The legs report owns --json; the flip read does not write to it.
    flip_rest, skip = [], False
    for x in rest:
        if skip:
            skip = False
        elif x == "--json":
            skip = True
        elif not x.startswith("--json="):
            flip_rest.append(x)
    # A book on the 160,000 path (stack_config_v2) always states the flip count: with none, one line "ARENA FLIP READ: 0 flipped legs, 0 SOL". Any other book
    # (another --expect-send) gets the legs output byte-identical and the legs rc.
    if a.expect_send != SEND_V2:
        return rc
    rc2 = arena_flip_read.main(["--bot", a.bot, "--ledger-root", a.ledger_root, "--expect-send", str(a.expect_send), *flip_rest], only_if_flips=True, zero_line=True)
    return rc or rc2


if __name__ == "__main__":
    raise SystemExit(report_main())
