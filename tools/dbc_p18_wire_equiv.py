#!/usr/bin/env python3
"""dbc_p18_wire_equiv: decision-equivalence proof for the P18 wiring (tools/dbc_p18_live.py). PAPER ONLY. md5 and counts only; nothing is priced.

Same harness as tools.dbc_p18_equiv (exploration hours [2026-10-14T00, 2026-10-17T00), same start row, same md5 of the decision log), with the engine feed replaced by the wiring:
every row passes through `ConfigGate` (its fetcher RAISES, so the proof also shows that no config on the tape needs a live fetch: all are in the committed map) and slot signals are
produced by synthetic `slotNotification` messages through `parse_slot_notification` -> `SlotSignals(lag)` -> `SlotCloser.signal`, for EVERY chain slot between two row slots.
The log md5 must equal the #1670 baseline 5053b05c3ff0446c1a07b692f15a3430 (pinned engine, pruning off) and the #978 `--signals` runs.

    python3 -m tools.dbc_p18_wire_equiv run --out DIR [--lag 2] [--limit-hours 72]
    python3 -m tools.dbc_p18_equiv compare DIR_A DIR_B --expect-md5 5053b05c3ff0446c1a07b692f15a3430
    python3 -m tools.dbc_p18_wire_equiv check DIR_A DIR_B      # QP pass criteria; exit 1 on any miss
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from tools import dbc_fwd as F
from tools import dbc_p18_equiv as EQ
from tools import dbc_p18_live as W
from tools import dbc_p18_run as P

BASELINE_MD5 = "5053b05c3ff0446c1a07b692f15a3430"
BASELINE_HEAD_SHA = "f3bc3700836fb089825b9abe4ef8569606275931267a961ca7ab3b12ea2c1627"  # #1670 / #1680 declog head
BASELINE_HEAD_SEQ, BASELINE_HOURS, BASELINE_DECISIONS = 1174, 72, 551  # #1670 / #1680


class FetchForbidden(AssertionError):
    pass


def _no_fetch(cfg: str) -> Any:
    raise FetchForbidden(cfg)


class WiredFeed:
    """Engine proxy for the proof (replaces tools.dbc_p18_equiv.SignalFeed): config gate on every row, a synthetic slotNotification for every chain slot, in order."""

    lag = W.CLOSE_LAG_SLOTS
    last_instance: "WiredFeed | None" = None

    def __init__(self, closer: P.SlotCloser, mode: int) -> None:
        self._c, self._last = closer, None
        self.gate = W.ConfigGate(_no_fetch, attempts=1)
        self.sigs = W.SlotSignals(self.lag)
        self.n_notifications = 0
        WiredFeed.last_instance = self

    def _notify(self, slot: int) -> None:
        raw = json.dumps({"jsonrpc": "2.0", "method": "slotNotification", "params": {"subscription": 2, "result": {"slot": slot, "parent": slot - 1, "root": slot - 32}}})
        s = W.parse_slot_notification(raw)
        assert s == slot
        self.n_notifications += 1
        sig = self.sigs.feed(s)
        if sig is not None:
            self._c.signal(sig)

    def on_row(self, r: dict[str, Any]) -> None:
        if r.get("type") in ("trade", "curve_complete"):
            assert self.gate.check(r) is None  # a config outside the map would need a live fetch: the fetcher raises
        s = P.SlotCloser.row_slot(r)
        if s is not None and self._last is not None and s > self._last:
            for t in range(self._last + 1, s + 1):
                self._notify(t)
        if s is not None and (self._last is None or s > self._last):
            self._last = s
        self._c.on_row(r)

    def __getattr__(self, k: str) -> Any:
        return getattr(self._c.eng, k)


def run(out: Path, lag: int, limit_hours: int, root: str | None = None) -> dict[str, Any]:
    WiredFeed.lag = lag
    old = EQ.SignalFeed
    EQ.SignalFeed = WiredFeed  # type: ignore[misc]
    try:
        res = EQ.run(EQ.START, min(EQ.END, EQ.START + timedelta(hours=limit_hours)), True, out, root, signals=2)
    finally:
        EQ.SignalFeed = old  # type: ignore[misc]
    wf = WiredFeed.last_instance
    res["wire"] = {"lag": lag, "notifications": wf.n_notifications if wf else None, "cfg_gate": dict(wf.gate.stats) if wf else None, "code_sha256": {
        f: F.sha256_file(Path(__file__).resolve().parent / f) for f in W.CODE_FILES}}
    (out / "equiv.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def check(dirs: list[Path]) -> dict[str, Any]:
    """QP pass criteria for the 72 h job (any miss = no registration at that sha). Reads only the equiv.json files of the runs."""
    here = Path(__file__).resolve().parent
    now_sha = {f: F.sha256_file(here / f) for f in W.CODE_FILES}
    fails: list[str] = []
    for d in dirs:
        r = json.loads((d / "equiv.json").read_text())
        w, sc = r.get("wire") or {}, r.get("slot_close") or {}
        gate = w.get("cfg_gate") or {}
        for name, ok in (("md5", r["declog"]["md5"] == BASELINE_MD5), ("head", r["declog"]["head"] == [BASELINE_HEAD_SHA, BASELINE_HEAD_SEQ]), ("hours", r["hours_streamed"] == BASELINE_HOURS),
                         ("decisions", r["decisions_live"] == BASELINE_DECISIONS), ("late_after_close", sc.get("late_after_close") == 0), ("late_rows", r["stats"].get("late_rows") == 0),
                         ("cfg_sights", gate.get("sights") == 0 and gate.get("rpc_calls") == 0), ("notifications", (w.get("notifications") or 0) > 0),
                         ("signals_advanced", (sc.get("signals_advanced") or 0) > 0), ("code_sha256", w.get("code_sha256") == now_sha)):
            if not ok:
                fails.append(f"{d.name}:{name}")
    return {"ok": not fails, "fails": fails, "dirs": [str(d) for d in dirs], "code_sha256": now_sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--root", default=None)
    r.add_argument("--lag", type=int, default=W.CLOSE_LAG_SLOTS)
    r.add_argument("--limit-hours", type=int, default=72)
    c = sub.add_parser("check")
    c.add_argument("dirs", nargs="+")
    ns = ap.parse_args(argv)
    if ns.cmd == "check":
        res = check([Path(x) for x in ns.dirs])
        print(json.dumps(res))
        return 0 if res["ok"] else 1
    try:
        res = run(Path(ns.out), ns.lag, ns.limit_hours, ns.root)
    except F.ReplayRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    print(json.dumps({k: res[k] for k in ("signals", "slot_close", "hours_streamed", "decisions_live", "peak_rss_mib", "wall_s", "declog", "wire")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
