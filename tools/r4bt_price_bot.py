"""r4bt_price_bot: runs `tools/r4bt_price.main` for a registered r4-b-T bot id (`--bot-id`, default `r4bt-a1`). PAPER ONLY.

`tools/r4bt_price.py` is pinned by the C0 twin and keeps its `r4bt-a1` default. This wrapper strips `--bot-id` from argv, checks it against the table of
`tools/r4bt_bots.py` (unknown ids refuse, exit 2), and runs the pricer's own `main` with `price` bound to that bot id for the one call. No pricing logic is here.
"""
from __future__ import annotations

import functools
import sys
from typing import Sequence

from tools import r4bt_bots as B
from tools import r4bt_gate as G
from tools import r4bt_price as P


def main(argv: Sequence[str] | None = None, **kw) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    bot = B.DEFAULT_BOT_ID
    if "--bot-id" in args:
        i = args.index("--bot-id")
        if i + 1 >= len(args):
            print("r4bt_price_bot: REFUSED: --bot-id needs a value", file=sys.stderr)
            return 2
        bot = args[i + 1]
        del args[i:i + 2]
    try:
        B.spec(bot)
    except G.Refused as e:
        print(f"r4bt_price_bot: REFUSED: {e}", file=sys.stderr)
        return 2
    orig = P.price
    P.price = functools.partial(orig, bot_id=bot)
    try:
        return P.main(args, **kw)
    finally:
        P.price = orig


if __name__ == "__main__":
    sys.exit(main())
