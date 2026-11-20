"""DRAIN-VETO trigger locator builder (QP-RULING-1120c, route (a), pinned).

Re-runs the exit study's L1 trigger pass COUNT-ONLY on the pinned kit, with no exit priced, and joins its triggers to the 332 keys of grid.pkl cell h24_tp1.5. Emits only the print locator
(mint, slot, tx_index, event_index) of each trigger. No amount, reserve, V, price, block_time, trader or outcome field is read into the output.

Pins, each checked by sha256 before anything else (refuse on mismatch): kit tools/arena_screen.py b37282c7 from a clean detached worktree at 0c8c987a; l1_rule.py cefdc4c6;
l1_rule_tpfix.py fba5dcc1 (hashed only, never imported: it patches pricing, which this pass does not run); the four aux_*.json; the three VMap files; grid.pkl fe86040a.
Grid reads (exhaustive): cells['h24_tp1.5'] keys blk, pool (crc32), sd, T. Anything else raises (GridView).
The L1 trigger rule is NOT re-implemented: LocRule.triggers is built at run time from inspect.getsource(L1Rule.triggers) with exactly one line inserted next to `out.append(`
(a test shows the diff is that one line). The parent's triggers is never called a second time on one state.
Reconciliation: rows_pumpswap, rows_non_wsol, rows_wsol_kept, empty_hours must equal run_grid.log's, and triggers_raw * 12 must equal 2136840. Any mismatch, any unmatched or
ambiguous key, or a count other than 332: NO file is written and the exit code is non-zero.
Join: key (blk, crc32(pool), sd) must match exactly one pool address, whose trigger print's hour start equals T. The locator is the first qualifying print in kit order in slot sd;
n_qual_in_slot counts the qualifying prints of that pool in that slot.
Completeness: a second pass over the trigger hours only reads key presence (pool, slot, tx_index, event_index) of every row of the matched pool in the slot; a missing or non-integer
key sets locator_ok false (txev_missing). mint '' -> mint_missing; rule's canon[mint] != pool -> mint_not_canon.
Output: one JSON line per trigger under /data2/mal/scratch/drainveto/, mode 0640, keys limited to OUT_KEYS; ordinals are assigned after sorting by (blk, slot, pool address), never in grid
order. It holds pool and mint addresses: an intermediate input, never quoted in a report. stdout carries counts only.
Disclosures D1-D6 (QP-RULING-1120c) are printed in DISCLOSURES and must be carried in every report that quotes step 0 or step 1.
Do not run beside another heavy job: one MiScusi job, nice 19, ionice idle, MemoryMax scope, at most 2 workers.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import inspect
import json
import os
import pickle
import re
import subprocess
import sys
import textwrap
import zlib
from datetime import datetime, timezone
from pathlib import Path

KIT_DIR = "/data2/mal/wt/floor-study"
KIT_HEAD = "0c8c987a820ab04130fbe0b416fe816d66a24ab1"
L4 = "/data/mal/hunt-1008/push-1016/hunt24/l4-audit"
CV = "/data/mal/clean-view"
OUT_ROOT = "/data2/mal/scratch/drainveto"
PINS = {
    f"{KIT_DIR}/tools/arena_screen.py": "b37282c7d23d3bfca310f2574b8bc2b9b0981e0e73ee2537e118f779244fbb57",
    f"{L4}/l1_rule.py": "cefdc4c676242d79ee667e32f82ee7c303532ada14ff81fd19d5c69b747daf58",
    f"{L4}/l1_rule_tpfix.py": "fba5dcc1e96f938af24af224f47c5174e71b84388d0708927aab5c6c12b623cc",
    f"{L4}/work/aux_explore.json": "1b77dc2010c645c255ec9b6d10ed53dc518f5ca267cc80fc0d528ce1c9879be0",
    f"{L4}/work/aux_fastpool.json": "c8076ba5a10f16dc17b73e29515cfe06624f56ef076a4320f00c6eb9c84ca655",
    f"{L4}/work/aux_oinsample.json": "861a54346e0561113d520458a59ba6ac22cd1d23bebe396b7797a69c21d71271",
    f"{L4}/work/aux_olive.json": "0d5b0d39512d12e2ef24bd3f00873fbacb422dbdd7bf5778d36067f0b941d824",
    "/data/mal/pumpswap-virtual/pool_v_0814.json": "2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8",
    "/data/mal/pumpswap-virtual/pool_v_0909.json": "70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e",
    "/data/mal/pumpswap-virtual/pool_v_exp016.json": "1f3e772d12cedbdb2dd860f619361fc0fdc88872fd5fa68639a11f91945162ec",
    "/data2/mal/scratch/floor-study/grid.pkl": "fe86040ad753c20719d1091a4a0e9c5f401f10ca80439e525b19e0091cb847a0",
}
GRID_PATH = "/data2/mal/scratch/floor-study/grid.pkl"
VM = ["/data/mal/pumpswap-virtual/pool_v_0814.json", "/data/mal/pumpswap-virtual/pool_v_0909.json", "/data/mal/pumpswap-virtual/pool_v_exp016.json"]
BLK = [("explore", f"{CV}/explore-0814"), ("fastpool", f"{CV}/fast-pool-2026-09-18T23_2026-09-22T00"), ("oinsample", f"{CV}/oracle-insample-2026-09-22_25"),
       ("olive", f"{CV}/oracle-live-2026-09-25_27")]
CELL = "h24_tp1.5"
GRID_KEYS = ("blk", "pool", "sd", "T")
EXPECT_N = 332
EXPECT_COUNTERS = {"rows_pumpswap": 512741427, "rows_non_wsol": 283970910, "rows_wsol_kept": 228770517, "empty_hours": 1}
EXPECT_TRIGGERS_X12 = 2136840
FANOUT = 12
OUT_KEYS = ("ordinal", "block", "blk", "pool", "pool_crc32", "mint", "hour", "slot", "tx_index", "event_index", "locator_ok", "locator_problem", "n_qual_in_slot")
PROBLEMS = ("none", "txev_missing", "mint_missing", "mint_not_canon")
ADDED_LINE = ("self.loc.append((str(hour.block), int(hour.T), pool, int(hour.sl[r]), int(hour.tx[r]), int(hour.ev[r]), str(hour.mints[p]), "
              "bool(canon.get(str(hour.mints[p])) == pool)))")
DISCLOSURES = (
    "D1 Trigger locator: a count-only re-run of the exit study's L1 trigger pass (kit 0c8c987a, arena_screen b37282c7, l1_rule cefdc4c6), joined to grid.pkl h24_tp1.5 keys (blk, pool crc32, sd, T); "
    "no exit priced and no outcome field read; 332/332 keys matched uniquely; reconciliation counters equal to the study run.",
    "D2 The trigger print is the first qualifying print in (slot, tx_index, event_index) order in slot sd, the one the kit priced. Give the count of triggers with more than one qualifying print in the slot.",
    "D3 The kit decided on all rows <= the decision slot, and its entry state is at sd + D. The drain reader cuts at the trigger print, so it uses no more information than the kit, and sometimes less.",
    "D4 locator_incomplete count per block (txev_missing, mint_missing, mint_not_canon); these triggers are censored.",
    "D5 The 332 are the study's priced set. Its membership already depends on exits (overlap after the previous trade's exit, block-edge exclusion) and on a 12-cell selection (h24_tp1.5). "
    "The veto is measured inside that set, and the selection is carried, not removed.",
    "D6 The mint comes from the PumpSwap trade row the kit loaded and is checked against the rule's own mint->pool map. oracle-live's mints are not checked against migrations; that block is censored as oracle_live_proxy anyway.",
)


class BuilderRefused(Exception):
    pass


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_pins(pins: dict) -> None:
    for path, want in pins.items():
        if not os.path.isfile(path):
            raise BuilderRefused(f"pin_missing: {path}")
        if sha256_file(path) != want:
            raise BuilderRefused(f"pin_sha_mismatch: {path}")


def check_kit_worktree(kit_dir: str, head: str) -> None:
    got = subprocess.run(["git", "-C", kit_dir, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", kit_dir, "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    if got != head or dirty:
        raise BuilderRefused(f"kit_worktree_not_pinned_or_dirty: head {got}")


# ------------------------------------------------------------------------------------------------------------------------------------------ the rule
def locrule_source(parent_src: str) -> str:
    """The parent's triggers source with exactly one line inserted right after its single `out.append(` line, same indent."""
    lines = textwrap.dedent(parent_src).split("\n")
    hits = [k for k, ln in enumerate(lines) if ln.strip().startswith("out.append(")]
    if len(hits) != 1:
        raise BuilderRefused(f"parent_triggers_unexpected: {len(hits)} out.append lines")
    ind = lines[hits[0]][: len(lines[hits[0]]) - len(lines[hits[0]].lstrip())]
    return "\n".join(lines[: hits[0] + 1] + [ind + ADDED_LINE] + lines[hits[0] + 1:])


def make_locrule(module):
    """module is the imported pinned l1_rule. Returns LocRule(L1Rule) whose triggers is the parent's source plus the one recording line."""
    L1Rule = module.L1Rule
    src = locrule_source(inspect.getsource(L1Rule.triggers))
    ns = dict(vars(module))
    exec(compile(src, "<locrule.triggers>", "exec"), ns)
    fn = ns["triggers"]

    class LocRule(L1Rule):
        name = "l1_locator"

        def __init__(self, **kw):
            super().__init__(**kw)
            self.loc: list = []

        triggers = fn

    return LocRule


# ------------------------------------------------------------------------------------------------------------------------------------------ grid
class GridView:
    """Read-only view of one grid cell that raises on any key outside GRID_KEYS."""

    def __init__(self, cell):
        self._c = cell

    def __getitem__(self, k):
        if k not in GRID_KEYS:
            raise BuilderRefused(f"grid_key_not_allowed: {k!r}")
        return self._c[k]


def project_grid(obj) -> list[tuple[int, int, int, int]]:
    """obj is the unpickled grid dict. Reads only cells[CELL][blk, pool, sd, T]; returns [(blk, crc32, sd, T)]."""
    v = GridView(obj["cells"][CELL])
    cols = [list(v[k]) for k in GRID_KEYS]
    n = len(cols[0])
    if any(len(c) != n for c in cols):
        raise BuilderRefused("grid_columns_unequal")
    return [(int(cols[0][i]), int(cols[1][i]), int(cols[2][i]), int(cols[3][i])) for i in range(n)]


def read_grid_keys(path: str) -> list:
    with open(path, "rb") as fh:
        obj = pickle.load(fh)
    keys = project_grid(obj)
    del obj
    return keys


# ------------------------------------------------------------------------------------------------------------------------------------------ join
def crc(pool: str) -> int:
    return zlib.crc32(pool.encode())


def join(recs: list, grid: list, block_names: list[str], expect_n: int = EXPECT_N) -> list[dict]:
    """recs: (block name, hour T, pool, slot, tx, ev, mint, canon_ok) in kit order. grid: (blk, crc32, sd, T). Returns locator dicts (unsorted) or raises with counts only."""
    if len(grid) != expect_n:
        raise BuilderRefused(f"grid_count_{len(grid)}_not_{expect_n}")
    if len(set((b, c, s) for b, c, s, _ in grid)) != len(grid):
        raise BuilderRefused("grid_keys_not_unique")
    by: dict = collections.defaultdict(list)
    for r in recs:
        by[(block_names.index(r[0]), crc(r[2]), r[3])].append(r)
    out, unmatched, ambiguous, hour_bad = [], 0, 0, 0
    for b, c, s, T in grid:
        cand = by.get((b, c, s), [])
        pools = {r[2] for r in cand}
        if not cand:
            unmatched += 1
            continue
        if len(pools) != 1:
            ambiguous += 1
            continue
        first = cand[0]  # kit order: the first qualifying print in the slot
        if int(first[1]) != T:
            hour_bad += 1
            continue
        out.append({"blk": b, "block": first[0], "pool": first[2], "pool_crc32": c, "slot": s, "hourT": int(first[1]), "tx_index": first[4], "event_index": first[5],
                    "mint": first[6], "canon_ok": first[7], "n_qual_in_slot": len(cand)})
    if unmatched or ambiguous or hour_bad or len(out) != expect_n:
        raise BuilderRefused(f"join_failed: matched {len(out)}/{expect_n}, unmatched {unmatched}, ambiguous {ambiguous}, hour_mismatch {hour_bad}")
    return out


_POOL_RE = re.compile(rb'"pool"\s*:\s*"([^"]+)"')


def _zstd_lines(path: str):
    z = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert z.stdout is not None
    try:
        yield from z.stdout
    finally:
        z.stdout.close()
        if z.wait() != 0:
            raise RuntimeError(f"zstd failed on {path}")


def scan_keys(path: str, wanted: dict, lines=_zstd_lines) -> dict:
    """wanted: pool -> set(slots). Reads only pool, slot, tx_index, event_index of rows of those pools. Returns (pool, slot) -> [n_rows, all_keys_present_as_ints]."""
    res = {(p, s): [0, True] for p, ss in wanted.items() for s in ss}
    for line in lines(path):
        m = _POOL_RE.search(line)
        if m is None:
            continue
        pool = m.group(1).decode()
        if pool not in wanted:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        r = {k: r.get(k) for k in ("slot", "tx_index", "event_index")}  # projection first
        if not isinstance(r["slot"], int) or r["slot"] not in wanted[pool]:
            continue
        cell = res[(pool, r["slot"])]
        cell[0] += 1
        for k in ("tx_index", "event_index"):
            if not isinstance(r[k], int) or isinstance(r[k], bool):
                cell[1] = False
    for cell in res.values():
        if cell[0] == 0:
            cell[1] = False
    return res


def finish_rows(loc: list[dict], keycheck: dict) -> list[dict]:
    """keycheck: (pool, slot) -> [n, ok]. Adds locator_ok / locator_problem, sorts by (blk, slot, pool), assigns ordinals, projects to OUT_KEYS."""
    rows = []
    for d in loc:
        ok_keys = keycheck.get((d["pool"], d["slot"]), [0, False])[1]
        problem = "none" if ok_keys else "txev_missing"
        if problem == "none" and d["mint"] == "":
            problem = "mint_missing"
        if problem == "none" and not d["canon_ok"]:
            problem = "mint_not_canon"
        hour = datetime.fromtimestamp(d["hourT"], tz=timezone.utc).strftime("%Y-%m-%dT%H")
        rows.append({**d, "hour": hour, "locator_ok": problem == "none", "locator_problem": problem})
    rows.sort(key=lambda d: (d["blk"], d["slot"], d["pool"]))
    out = []
    for i, d in enumerate(rows, 1):
        d["ordinal"] = i
        out.append({k: d[k] for k in OUT_KEYS})
    return out


def write_rows(rows: list[dict], out_dir: str, root: str = OUT_ROOT) -> tuple[str, str]:
    real = os.path.realpath(out_dir)
    if real != os.path.realpath(root):
        raise BuilderRefused(f"out_dir_not_{root}: {out_dir}")
    for r in rows:
        if tuple(r) != OUT_KEYS:
            raise BuilderRefused("output_key_not_allowed")
    os.makedirs(real, exist_ok=True)
    path = os.path.join(real, "drainveto_triggers.jsonl")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=False) + "\n")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)
    return path, sha256_file(path)


def counts_only(rows: list[dict]) -> dict:
    out = {"triggers": len(rows), "by_block": {}, "multi_qual_in_slot": 0}
    for r in rows:
        b = out["by_block"].setdefault(r["block"], {"matched": 0, "txev_missing": 0, "mint_missing": 0, "mint_not_canon": 0})
        b["matched"] += 1
        if r["locator_problem"] != "none":
            b[r["locator_problem"]] += 1
        out["multi_qual_in_slot"] += int(r["n_qual_in_slot"] > 1)
    return out


def reconcile(counters: dict) -> None:
    bad = {k: counters.get(k) for k, v in EXPECT_COUNTERS.items() if counters.get(k) != v}
    if bad or counters.get("triggers_raw", 0) * FANOUT != EXPECT_TRIGGERS_X12:
        raise BuilderRefused(f"reconciliation_failed: {bad} triggers_raw*12={counters.get('triggers_raw', 0) * FANOUT}")


# ------------------------------------------------------------------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DRAIN-VETO trigger locator builder (pinned, count-only)")
    ap.add_argument("--out-dir", default=OUT_ROOT)
    a = ap.parse_args(argv)
    try:
        sys.dont_write_bytecode = True
        check_pins(PINS)
        check_kit_worktree(KIT_DIR, KIT_HEAD)
        grid = read_grid_keys(GRID_PATH)
        if len(grid) != EXPECT_N:
            raise BuilderRefused(f"grid_count_{len(grid)}_not_{EXPECT_N}")
        sys.path.insert(0, KIT_DIR)
        sys.path.insert(1, L4)
        import importlib
        S = importlib.import_module("tools.arena_screen")
        if os.path.realpath(S.__file__) != os.path.realpath(f"{KIT_DIR}/tools/arena_screen.py"):
            raise BuilderRefused(f"kit_module_not_pinned: {S.__file__}")
        l1 = importlib.import_module("l1_rule")
        if os.path.realpath(l1.__file__) != os.path.realpath(f"{L4}/l1_rule.py"):
            raise BuilderRefused("l1_rule_module_not_pinned")
        Loc = make_locrule(l1)
        rule = Loc(aux_dir=f"{L4}/work")
        vmap = S.VMap(VM)
        scr = S.Screen(rule, count_only=True, screen_p=False)
        names, files_by_block = [], {}
        for bi, (_, path) in enumerate(BLK):
            S.check_block_path(path, True)
            name = os.path.relpath(os.path.realpath(path), CV)
            files = S.discover(path, "clean", None)
            names.append(name)
            files_by_block[name] = {int(t.timestamp()): f for t, f in files}
            scr.run_block(bi, name, S.iter_hours(files, name, "backfill", vmap, 2))
            print("block done", name, flush=True)
        reconcile(dict(scr.counters))
        loc = join(rule.loc, grid, names)
        wanted: dict = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(set)))
        for d in loc:
            wanted[d["block"]][d["hourT"]][d["pool"]].add(d["slot"])
        keycheck: dict = {}
        for name, hours in wanted.items():
            for hT, pools in hours.items():
                f = files_by_block[name].get(hT)
                if f is None:
                    raise BuilderRefused("trigger_hour_file_missing")
                keycheck.update(scan_keys(f, {p: set(s) for p, s in pools.items()}))
        rows = finish_rows(loc, keycheck)
        path, sha = write_rows(rows, a.out_dir)
        print(json.dumps(counts_only(rows), sort_keys=True))
        print("reconciliation PASS", {k: dict(scr.counters).get(k) for k in EXPECT_COUNTERS})
        print("trigger file sha256", sha)
        for d in DISCLOSURES:
            print(d)
        return 0
    except BuilderRefused as e:
        print(f"REFUSED (no file written): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
