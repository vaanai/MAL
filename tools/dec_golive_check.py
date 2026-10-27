"""Count the OPEN placeholder values in a canary DEC copy: `FILL:gate-read`, `FILL:review`, `FILL:owner` (values, not the bare `FILL:` field names
of the definitions table), and say whether the owner line is present. Read-only. Usage: python -m tools.dec_golive_check DEC/DEC-033-h5v1q50-canary.md"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from tools import h5_arena_executor as ax

OPEN_RE = re.compile(r"FILL:(gate-read|review|owner)(?![\w-])")
FIELD_RE = re.compile(r"`FILL:[A-Za-z_@0-9]+`")  # a field NAME in backticks is a definition, not an open value


def open_values(text: str) -> dict[str, int]:
    out = {"gate-read": 0, "review": 0, "owner": 0}
    for m in OPEN_RE.finditer(FIELD_RE.sub("", text)):
        out[m.group(1)] += 1
    return out


def main(argv: list[str]) -> int:
    p = Path(argv[0])
    text = p.read_text()
    c = open_values(text)
    owner_line = "h5v1q50-boostfloor-a1" in ax.CANARY_OWNER_RE.findall(text)
    print({"open": c, "total_open": sum(c.values()), "owner_line_present": owner_line})
    return 0 if sum(c.values()) == 0 and owner_line else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
