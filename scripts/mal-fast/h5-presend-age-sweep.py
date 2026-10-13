#!/usr/bin/env python3
"""Decision-equivalence sweep for the H5 executor's pre-send trigger age limit (PR #622).

Runs the repo's own executor test fixtures (tools/test_h5_executor.py `Env`, a fake RPC and clock) on a BASE tree and a HEAD tree, one fresh executor per
trigger, over a fixed grid of trigger ages, and compares the decisions (the refusal names and whether a buy was sent).
Reads repo fixtures only: no ledger, shadow, sealed or forward data, no network, no key.

    h5-presend-age-sweep.py --base <git sha | dir> --head <git sha | dir | .> [--repo <git dir>]

A git sha is exported with `git archive` into a temp dir. Grid: decision age 0..10_000 ms in 100 ms steps (101), block_time age 0..11 s (12),
chain age 0..50 slots at sps 0.2 (51): 164 triggers. Needs the interpreter that has `solders` (e.g. /data/mal/venv/bin/python).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


def child(root: str) -> None:
    """Run the grid on the tree at `root`; print the rows as JSON."""
    from unittest import mock

    sys.path.insert(0, root)
    from tools import h5_executor as h
    from tools import probe_executor as pe
    from tools.test_h5_executor import T0, Env

    tmp = Path(tempfile.mkdtemp())
    etc = tmp / "etc-mal-h5"
    etc.mkdir()
    os.chmod(etc, 0o755)
    pd = tmp / "probe-live-dir"
    pd.mkdir()
    for target, attr, val in ((h, "LIVE_OK_PATH", etc / "LIVE_OK"), (h, "TIER_FILE_PATH", etc / "TIER"), (h, "LIVE_OK_UID", os.getuid()),
                              (h, "LIVE_OK_GID", os.getgid()), (pe, "LIVE_DIR", pd)):
        mock.patch.object(target, attr, val).start()
    rows = []

    def one(kind, k, mk):
        sub = tmp / f"{kind}{k}"
        sub.mkdir()
        e = Env(sub)
        e.fire(**mk(e))
        rows.append([kind, k, e.refusals(), len(e.rpc.sent)])

    for a in range(0, 10_001, 100):
        one("decision_ms", a, lambda e, a=a: {"decision_ms": T0 - a})
    for s in range(0, 12):
        one("block_time_s", s, lambda e, s=s: {"block_time": T0 // 1000 - s})
    for n in range(0, 51):
        one("chain_slots", n, lambda e, n=n: {"trigger_slot": e.rpc.slot - n, "s0_slot": e.rpc.slot - n - 500})
    print(json.dumps({"limit": h.H5Limits.from_config({}).max_trigger_age_ms, "T0_mod_1000": T0 % 1000, "rows": rows}))


def tree(spec: str, repo: str, work: Path, name: str) -> str:
    if os.path.isdir(spec):
        return str(Path(spec).resolve())
    dest = work / name
    dest.mkdir()
    arc = subprocess.run(["git", "-C", repo, "archive", spec, "tools", "scripts", "EXP", "DEC", "docs"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=arc, check=True)
    return str(dest)


def run(root: str) -> dict:
    out = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", root], capture_output=True, text=True, cwd=root)
    if out.returncode:
        sys.exit(out.stderr[-2000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


def md5(x) -> str:
    return hashlib.md5(json.dumps(x).encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--child")
    a = ap.parse_args()
    if a.child:
        child(a.child)
        return
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        res = {name: run(tree(spec, a.repo, work, name)) for name, spec in (("base", a.base), ("head", a.head))}
    b, h = res["base"]["rows"], res["head"]["rows"]
    assert [r[:2] for r in b] == [r[:2] for r in h]
    for name in ("base", "head"):
        r = res[name]["rows"]
        buys = sum(1 for x in r if x[3] == 1)
        print(f"{name}: limit={res[name]['limit']} triggers={len(r)} buys={buys} refusals={len(r) - buys} T0%1000={res[name]['T0_mod_1000']}")
        print(f"  refusal names: {dict(Counter(n for x in r for n in x[2]))}")
        print(f"  md5 of the decision list: {md5(r)}")
    b2r = [(x[:2], y[2]) for x, y in zip(b, h) if x[3] == 1 and y[3] == 0]
    r2b = [x[:2] for x, y in zip(b, h) if x[3] == 0 and y[3] == 1]
    same = [x for x, y in zip(b, h) if x[3] == 1 and y[3] == 1 and x == y]
    print(f"buy->refusal: {len(b2r)}  refusal->buy: {len(r2b)}  unchanged buys: {len(same)}")
    by_kind = Counter(k[0] for k, _ in b2r)
    print(f"  buy->refusal by grid: {dict(by_kind)}; names {dict(Counter(n for _, ns in b2r for n in ns))}")
    print(f"  unchanged buys by grid: {dict(Counter(x[0] for x in same))}")
    print(f"  md5 of the unchanged-buy subset (base == head by construction): {md5(same)}")
    print("  refusal->buy rows:", r2b)
    sys.exit(1 if r2b else 0)


main()
