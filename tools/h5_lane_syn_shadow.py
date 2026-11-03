"""H5 shadow for the DEC-035 lane book `h5-lane-syn`: H5-BOOSTFLOOR v1 (Q* 40) on SYNTHETIC-migration pools only. New module; tools/h5_shadow.py and
tools/h5_lane_shadow.py are NOT edited (their blobs are pinned; see tools/test_h5_lane_syn.py).

Ruling: /data/mal/hunt-1008/ops/manager19/QP-RULING-1103-synthetic-lane.md (C1, C3, C7). What differs from `python -m tools.h5_lane_shadow`:

  - SynEngine (a subclass of tools.h5_shadow.Engine) is installed in place of Engine for the run (run_live builds `Engine(` by global name, like the lane
    wrapper's oracle). Its `_fire` writes a trigger ONLY when the pool's class is the literal True with `synthetic_src` in {"ws", "rpc"} AND the pool's
    s0 block time is at or after SYN_CUT_MS (2026-11-06T00:00:00Z). The stock `_fire` writes the record: during that call `_syn` answers the flipped class so the
    decision fields are those of the stock code (a drill compares their md5), and `emit` then sets `synthetic` true, `population` "synthetic_only" and adds
    `s0_block_time` (the pool's s0 block time in seconds, for the executor's own re-check). Nothing else in the record changes.
  - Everything else is an `excluded` record, no Q, price or V: reason `plain` (class False), `unclassified` (None, or True with a source that cannot write True),
    `syn_pre_window` (s0 block time missing or before the cut). No trigger record in this dir ever says synthetic false or null (emit drops one that does).
  - The H5 Look-2 seal is NOT disabled: h5_look2_start_ms stays the stock value, `--h5-look2-observed` is refused, so for s0 >= 2026-11-06T00Z the stock
    code withholds every outcome, ladder, strip and min_q (no paper outcome record is written; the live-vs-sim gap is "no sim twin (shadow seal)").
  - The start record carries seal.population = "synthetic_only", seal.lane = "DEC-035", bot_id "h5-lane-syn".
  - Out dir: exactly $HOME/data/h5-shadow-lane-syn. `--rpc-url` is REQUIRED and must differ from the stock default, so this shadow does not share or
    starve h5-lane-2's public-RPC classifier budget (ruling C7). Q* 40 only; `--fresh-forward` and `--q-star-sol` are refused.
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools import h5_lane_shadow as ls
from tools import h5_shadow as hs

BOT_ID = "h5-lane-syn"
POPULATION = "synthetic_only"  # the tag on the start record, every trigger and every lane-ledger row (ruling C3/C5)
POPULATION_SEAL = POPULATION
SYN_CUT_MS = int(datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)  # 1793923200000
SYN_SRC_OK = ("ws", "rpc")  # the only sources that can write True (h5_shadow.py SynClassifier)
OUT_DIR_NAME = "h5-shadow-lane-syn"
WRAPPER_MODULE = "tools.h5_lane_syn_shadow"


def wrapper_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def s0_ms_of(s0_block_time: Any) -> int | None:
    """Pool s0 block time (seconds) -> ms, or None when missing / not a positive number."""
    if isinstance(s0_block_time, bool) or not isinstance(s0_block_time, (int, float)) or s0_block_time <= 0:
        return None
    return int(s0_block_time * 1000)


def after_cut(s0_block_time: Any) -> bool:
    t = s0_ms_of(s0_block_time)
    return t is not None and t >= SYN_CUT_MS


def trigger_ok(rec: dict[str, Any]) -> bool:
    return (rec.get("synthetic") is True and rec.get("synthetic_src") in SYN_SRC_OK and rec.get("population") == POPULATION
            and after_cut(rec.get("s0_block_time")))


class SynEngine(hs.Engine):
    def __init__(self, *a: Any, **kw: Any) -> None:
        if kw.get("h5_look2_start_ms", hs.H5_LOOK2_START_MS) is None or kw.get("h5_look2_observed"):
            raise ValueError("SynEngine keeps the H5 Look-2 seal on (QP ruling 1103 C3)")
        super().__init__(*a, **kw)
        self._flip: tuple[str, Any] | None = None

    def _syn(self, p: Any) -> tuple[bool | None, str | None]:
        if self._flip is not None:
            return False, self._flip[0]  # only inside the stock _fire, only for a pool that passed every gate below
        return super()._syn(p)

    def _fire(self, p: Any, pr: Any, idx: int, var: str, sps: float, t: float, spent: int, ident: str | None, src: str) -> None:
        if self._sealed(p) or p.excluded is not None:
            return super()._fire(p, pr, idx, var, sps, t, spent, ident, src)  # the CAP-PICK stub / earlier exclusion: stock behaviour, never a trigger
        syn, ssrc = hs.Engine._syn(self, p)
        if syn is False:
            return self._exclude(p, pr, var, "plain")
        if syn is not True or ssrc not in SYN_SRC_OK:
            return self._exclude(p, pr, var, "unclassified")
        if not after_cut(p.s0_ts):
            return self._exclude(p, pr, var, "syn_pre_window")
        self._flip = (ssrc, p.s0_ts)
        try:
            super()._fire(p, pr, idx, var, sps, t, spent, ident, src)
        finally:
            self._flip = None

    def emit(self, rec: dict) -> None:
        t = rec.get("type")
        if t == "trigger":
            if self._flip is None:
                self.counters["syn_trigger_dropped_no_gate"] += 1
                return
            rec["synthetic"] = True
            rec["population"] = POPULATION
            rec["s0_block_time"] = self._flip[1]
            if not trigger_ok(rec):
                self.counters["syn_trigger_dropped_bad"] += 1
                return
        elif t == "start":
            seal = dict(rec.get("seal") or {})
            seal.update({"population": POPULATION_SEAL, "lane": ls.LANE_REF})
            rec["seal"] = seal
            rec["bot_id"] = BOT_ID
            rec["syn_cut_ms"] = SYN_CUT_MS
            rec["wrapper_module"] = WRAPPER_MODULE  # the executor re-checks module identity and file hash against its own checkout
            rec["wrapper_sha256"] = wrapper_sha256()
        super().emit(rec)


def check_syn_out_dir(path: str | None) -> None:
    ls.check_lane_out_dir(path)
    if Path(os.path.realpath(path or "")).name != OUT_DIR_NAME:
        raise ValueError(f"h5-lane-syn shadow out dir must be $HOME/data/{OUT_DIR_NAME}")


def _flag_values(args: Sequence[str], name: str) -> list[str | None]:
    out: list[str | None] = []
    for i, a in enumerate(args):
        if a == name:
            out.append(args[i + 1] if i + 1 < len(args) else None)
        elif a.startswith(name + "="):
            out.append(a.split("=", 1)[1])
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    for bad in ("--h5-look2-observed", "--fresh-forward", "--q-star-sol"):
        if _flag_values(args, bad):
            print(f"h5_lane_syn_shadow: {bad} is refused for this book", file=sys.stderr)
            return 2
    outs = _flag_values(args, "--out-dir")
    try:
        check_syn_out_dir(outs[-1] if outs else None)
    except ValueError as exc:
        print(f"h5_lane_syn_shadow: {exc}", file=sys.stderr)
        return 2
    rpcs = _flag_values(args, "--rpc-url")
    if not rpcs or not rpcs[-1] or rpcs[-1] == hs.DEFAULT_RPC:
        print("h5_lane_syn_shadow: --rpc-url is required and must differ from the stock default (separate classifier budget from h5-lane-2)", file=sys.stderr)
        return 2
    # drop any --lane flag (the wrapper supplies the one value the lane shadow accepts)
    cleaned: list[str] = []
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a == "--lane":
            skip = True
            continue
        if a.startswith("--lane="):
            continue
        cleaned.append(a)
    rest = cleaned
    orig_engine, orig_oracle = hs.Engine, hs.cap_pick_oracle_from_env
    hs.Engine = SynEngine  # type: ignore[misc]  # run_live builds `Engine(` by this global name
    try:
        return ls.main(["--lane", ls.LANE_REF, *rest])
    finally:
        hs.Engine = orig_engine  # type: ignore[misc]
        hs.cap_pick_oracle_from_env = orig_oracle  # type: ignore[assignment]


if __name__ == "__main__":
    sys.exit(main())
