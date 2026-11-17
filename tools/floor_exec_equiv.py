"""floor_exec_equiv: md5 decision-equivalence proof for the FLOOR lane executor between two code trees (DRY mode, no network, no key).

`run` replays ONE private copy of a decision-log root through each tree's `python <shim> run --config <dry config> --once` (cwd and PYTHONPATH = the tree; the shim calls
tools.floor_lane_executor.main), with an IDENTICAL generated config (no presend_batch key) and a FIXED wall clock (the shim pins time.time; the executor's `now_ms` default is
int(time.time()*1000); time.sleep is a no-op). Every .jsonl the run wrote under its state dir (bridge log) and the lane ledger path (if written) is normalised and md5'd: rows
are parsed and ONLY keys that exist in tree B's rows and not in tree A's AND whose name starts with `stage_` or equals `presend_batch` or `exec_stage_ms` are dropped (at any depth); keys are
sorted; row order is kept.

`readers` checks that the repo's readers of bridge rows give the same output for a buy_sent row with and without the new keys.

Not proven: live behaviour, the presend_batch=true path, real RPC timing. With a fixed clock the entry-age gate sees the same age in both trees; pick --clock-ms near the
newest row for a meaningful replay (default: newest row ms + 1000). Not gate evidence, not a profit claim.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

BOT = "l1-floor-v2"
FROZEN_SHA = "5e12f8f597571d6d5191a7ba38125c67ad723b7d"
DEFAULT_BALANCE = 600_000_000
LANE = {"stake_lamports": 250_000_000, "budget_lamports": 2_100_000_000, "daily_loss_lamports": 500_000_000, "max_open": 16}
RPC_RPS = 4.0
SHIM = (
    "import os, sys, time\n"
    "_T = int(os.environ['FLOOR_EQUIV_CLOCK_MS']) / 1000.0\n"
    "time.time = lambda: _T\n"
    "time.sleep = lambda s: None\n"
    "from tools import floor_lane_executor as X\n"
    "sys.exit(X.main(sys.argv[1:]))\n"
)
NEW_KEYS = {"stage_ms": {"checked": 1, "prefetch": 2, "reads": 3, "wa": 4, "buy_done": 5}, "stage_note": "x"}


def gen_config(root: Path, state_dir: Path, work: Path, end_ms: int, balance: int) -> dict[str, Any]:
    """Same values for both trees. The field set of a lane config; no presend_batch, no a3 record, no live_ok."""
    return {"mode": "dry", "bot_id": BOT, "frozen_sha": FROZEN_SHA, "root": str(root), "state_dir": str(state_dir), "end_ms": end_ms, "rpc_rps": RPC_RPS,
            "dry_balance_lamports": balance,
            "lane": {**LANE, "ledger_path": str(work / "lane-ledger.jsonl"), "stop_path": str(work / "LANE-STOP")}}


def max_row_ms(root: Path) -> int:
    best = 0
    for f in (root / "decisions" / BOT).glob("*.jsonl"):
        for line in f.read_text().splitlines():
            try:
                best = max(best, int(json.loads(line).get("ms") or 0))
            except (ValueError, TypeError, AttributeError):
                continue
    return best


def run_tree(tree: Path, root_copy: Path, out: Path, label: str, clock_ms: int, balance: int) -> dict[str, Any]:
    work = out / label
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    root = work / "root"
    shutil.copytree(root_copy, root)
    state = work / "state"
    cfg_path = work / "config.json"
    cfg_path.write_text(json.dumps(gen_config(root, state, work, clock_ms + 86_400_000, balance), sort_keys=True, indent=1))
    shim = work / "shim.py"
    shim.write_text(SHIM)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(tree), "FLOOR_EQUIV_CLOCK_MS": str(clock_ms), "PYTHONHASHSEED": "0", "HOME": str(work)}
    p = subprocess.run([sys.executable, str(shim), "run", "--config", str(cfg_path), "--once"], cwd=tree, env=env, capture_output=True, text=True, timeout=600)
    (work / "stdout.txt").write_text(p.stdout)
    (work / "stderr.txt").write_text(p.stderr)
    ledger = work / "lane-ledger.jsonl"
    files = sorted(state.rglob("*.jsonl")) + ([ledger] if ledger.exists() else [])
    streams = {str(f.relative_to(work)): [json.loads(x) for x in f.read_text().splitlines() if x.strip()] for f in files}
    return {"rc": p.returncode, "streams": streams, "start_line": (p.stdout.splitlines() or [""])[0], "stderr_tail": p.stderr[-300:]}


def all_keys(o: Any, acc: set[str]) -> set[str]:
    if isinstance(o, dict):
        for k, v in o.items():
            acc.add(k)
            all_keys(v, acc)
    elif isinstance(o, list):
        for v in o:
            all_keys(v, acc)
    return acc


def allowed_name(k: str) -> bool:
    return k.startswith("stage_") or k in ("presend_batch", "exec_stage_ms")  # exec_stage_ms: the executor-side stamp dict PR #1409/#1410 adds beside stage_ms


def strip(o: Any, drop: set[str]) -> Any:
    if isinstance(o, dict):
        return {k: strip(v, drop) for k, v in o.items() if k not in drop}
    if isinstance(o, list):
        return [strip(v, drop) for v in o]
    return o


def dropped_keys(rows_a: list[Any], rows_b: list[Any]) -> list[str]:
    ka: set[str] = set()
    kb: set[str] = set()
    for r in rows_a:
        all_keys(r, ka)
    for r in rows_b:
        all_keys(r, kb)
    return sorted(k for k in kb - ka if allowed_name(k))


def md5_streams(s: dict[str, list[Any]]) -> str:
    h = hashlib.md5()
    for name in sorted(s):
        h.update(name.encode() + b"\n")
        for r in s[name]:
            h.update(json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    return h.hexdigest()


def kinds(s: dict[str, list[Any]]) -> dict[str, int]:
    return dict(sorted(Counter(f"{k}:{r.get('kind', r.get('ev', '?')) if isinstance(r, dict) else '?'}" for k in s for r in s[k]).items()))


def compare(a: dict[str, list[Any]], b: dict[str, list[Any]]) -> dict[str, Any]:
    drop = set(dropped_keys([r for k in a for r in a[k]], [r for k in b for r in b[k]]))
    nb = {k: [strip(r, drop) for r in v] for k, v in b.items()}
    ma, mb = md5_streams(a), md5_streams(nb)
    return {"rows_by_kind_a": kinds(a), "rows_by_kind_b": kinds(b), "dropped_keys": sorted(drop), "md5_a": ma, "md5_b": mb, "streams_a": sorted(a), "streams_b": sorted(b), "equal": ma == mb}


def cmd_run(a: argparse.Namespace) -> int:
    out, rc = Path(a.out), Path(a.root_copy)
    out.mkdir(parents=True, exist_ok=True)
    clock = a.clock_ms if a.clock_ms is not None else max_row_ms(rc) + 1_000
    ra = run_tree(Path(a.tree_a).resolve(), rc, out, "tree_a", clock, a.balance)
    rb = run_tree(Path(a.tree_b).resolve(), rc, out, "tree_b", clock, a.balance)
    res = compare(ra["streams"], rb["streams"])
    nonempty = sum(len(v) for v in ra["streams"].values()) > 0
    res.update({"schema": "floor_exec_equiv_v1", "clock_ms": clock, "balance_lamports": a.balance, "rc_a": ra["rc"], "rc_b": rb["rc"], "start_line_a": ra["start_line"],
                "start_line_b": rb["start_line"], "config_presend_batch": "absent", "nonempty": nonempty})
    if ra["rc"] or rb["rc"]:
        res["stderr_tail_a"], res["stderr_tail_b"] = ra["stderr_tail"], rb["stderr_tail"]
    res["equal"] = bool(res["equal"] and ra["rc"] == 0 and rb["rc"] == 0 and nonempty)
    print(json.dumps(res, sort_keys=True, indent=1))
    return 0 if res["equal"] else 1


# --- readers -------------------------------------------------------------------------------------------------------------------------------------------------


def _row(**kw: Any) -> dict[str, Any]:
    return {"schema": "arena_live_bridge_v1", "ts_ms": 1_791_300_001_000, "bot_id": BOT, "lane_bot_id": "floor-lane-1", "mode": "live", "order_id": "o1", **kw}


def sample_rows() -> list[dict[str, Any]]:
    return [_row(kind="buy_sent", decision_ms=1_791_300_000_900, send_ms=1_791_300_001_000, decision_to_send_ms=100, stake_lamports=250_000_000, decision_slot=1000, label="ok"),
            _row(kind="buy_landed", ts_ms=1_791_300_002_000, landed_slot=1002, decision_slot=1000)]


def with_new_keys(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**r, **NEW_KEYS} if r["kind"] == "buy_sent" else dict(r) for r in rows]


def _load_script(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def reader_checks() -> list[tuple[str, Any]]:
    """(name, fn(rows) -> output). Each fn is fed the bridge rows and must give the same output with and without the new keys."""
    repo = Path(__file__).resolve().parent.parent
    checks: list[tuple[str, Any]] = []

    def lane_gap(rows: list[dict[str, Any]]) -> Any:
        from tools import lane_daily_report as L

        return json.dumps(L.gap_report("floor-lane-1", rows, 0, 2_000_000_000_000), sort_keys=True)

    def lane_read(rows: list[dict[str, Any]]) -> Any:
        from tools import lane_daily_report as L

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            return json.dumps(L.gap_report("floor-lane-1", L.read_jsonl(p), 0, 2_000_000_000_000), sort_keys=True)

    def exp029(rows: list[dict[str, Any]]) -> Any:
        from tools import exp029_read as E

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            return json.dumps(E.read_bridge([p], []), sort_keys=True, default=str)

    def osb_iter(rows: list[dict[str, Any]]) -> Any:
        m = _load_script(repo / "scripts" / "ops" / "osb_live_extract.py", "osb_live_extract_equiv")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            st: dict[str, int] = {}
            got = list(m.iter_rows(str(p), st, "bridge", kinds=m.BRIDGE_KINDS))
            return json.dumps([{k: r.get(k) for k in ("order_id", "ts_ms", "decision_ms", "send_ms", "stake_lamports", "decision_slot", "label", "mode", "kind")} for r in got], sort_keys=True) + json.dumps(st, sort_keys=True)

    checks += [("tools/lane_daily_report.gap_report", lane_gap), ("tools/lane_daily_report.read_jsonl + gap_report", lane_read), ("tools/exp029_read.read_bridge", exp029),
               ("scripts/ops/osb_live_extract.iter_rows (live extract bridge reader, bridge fields it keeps)", osb_iter)]
    return checks


def cmd_readers(a: argparse.Namespace) -> int:
    base = sample_rows()
    new = with_new_keys(base)
    results = []
    for name, fn in reader_checks():
        try:
            ra, rb = fn(base), fn(new)
            results.append({"reader": name, "ok": ra == rb, "detail": "" if ra == rb else f"{ra[:200]!r} != {rb[:200]!r}"})
        except Exception as exc:  # a reader that raises on (or without) the new keys fails the check
            results.append({"reader": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
    print(json.dumps({"schema": "floor_exec_equiv_readers_v1", "new_keys": sorted(NEW_KEYS), "readers": results, "all_ok": all(r["ok"] for r in results)}, indent=1, sort_keys=True))
    return 0 if all(r["ok"] for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="md5 decision-equivalence proof for the FLOOR lane executor between two code trees")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tree-a", required=True)
    r.add_argument("--tree-b", required=True)
    r.add_argument("--root-copy", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--balance", type=int, default=DEFAULT_BALANCE)
    r.add_argument("--clock-ms", type=int, default=None)
    sub.add_parser("readers")
    a = ap.parse_args(argv)
    return cmd_run(a) if a.cmd == "run" else cmd_readers(a)


if __name__ == "__main__":
    sys.exit(main())
