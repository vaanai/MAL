#!/usr/bin/env python3
"""EXP-012 entry-latency curve under PumpSwap virtual-reserve (V) pricing. EXPLORATION, NOT EVIDENCE.

Runs `tools.exp012_latency_sensitivity` unchanged, with its pool workers wrapped by
`tools.pumpswap_virtual_adapter.patched_pool_workers` (V added to PumpSwap prints in-process,
`mcap_mode="v"`), so execution is priced on vault + V. Selection is the stored OOF score, the same as the
V-less curve (ARTIFACTS/lab/exp012-latency-sensitivity-2026-10-02.md). Same 9-day exploration pool;
never reads a holdout or the forward directories (the latency tool's own root guard applies).

    python -m tools.exp012_latency_virtual --out-dir DIR [latency-tool args...] [--vmap PATH]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence

DEFAULT_VMAP = "/data/mal/pumpswap-virtual/pool_v.json"


def set_env(vmap: str, counts: Path) -> None:
    from tools import pumpswap_virtual_adapter as ad

    counts.mkdir(parents=True, exist_ok=True)
    os.environ[ad.ENV_MAP] = vmap
    os.environ[ad.ENV_MCAP] = "v"
    os.environ[ad.ENV_COUNTS] = str(counts)
    os.environ[ad.ENV_CAPTURE] = "0"
    os.environ[ad.ENV_FROZEN] = "0"


def split_args(argv: Sequence[str]) -> tuple[str, list[str], Path]:
    rest = list(argv)
    vmap = DEFAULT_VMAP
    if "--vmap" in rest:
        i = rest.index("--vmap")
        vmap = rest[i + 1]
        del rest[i : i + 2]
    if "--out-dir" not in rest:
        raise SystemExit("--out-dir is required")
    out = Path(rest[rest.index("--out-dir") + 1])
    return vmap, rest, out


def main(argv: Sequence[str] | None = None) -> int:
    from tools import exp012_latency_sensitivity as ls
    from tools import pumpswap_virtual_adapter as ad

    vmap, rest, out = split_args(sys.argv[1:] if argv is None else argv)
    set_env(vmap, out / "counts_virtual")
    with ad.patched_pool_workers():
        return ls.main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
