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
    "from pathlib import Path\n"
    "from tools import floor_lane_executor as X\n"
    "import functools\n"
    "X.FloorLaneBridge = functools.partial(X.FloorLaneBridge, tier_path=Path(os.environ['FLOOR_EQUIV_TIER']))\n"
    "sys.exit(X.main(sys.argv[1:]))\n"
)
NEW_KEYS = {"stage_ms": {"checked": 1, "prefetch": 2, "reads": 3, "wa": 4, "buy_done": 5}, "stage_note": "x", "exec_stage_ms": {"state_ms": 1, "built_ms": 2}, "presend_batch": False}


def gen_config(root: Path, state_dir: Path, work: Path, end_ms: int, balance: int) -> dict[str, Any]:
    """Same values for both trees. The field set of a lane config; no presend_batch, no a3 record, no live_ok."""
    return {"mode": "dry", "bot_id": BOT, "frozen_sha": FROZEN_SHA, "root": str(root), "state_dir": str(state_dir), "end_ms": end_ms, "rpc_rps": RPC_RPS,
            "dry_balance_lamports": balance,
            "stop_file": str(work / "STOP"), "halt_file": str(work / "HALT"), "live_ok_path": str(work / "LIVE_OK"),
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
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(tree), "FLOOR_EQUIV_CLOCK_MS": str(clock_ms), "FLOOR_EQUIV_TIER": str(work / "TIER"), "PYTHONHASHSEED": "0", "HOME": str(work)}
    p = subprocess.run([sys.executable, str(shim), "run", "--config", str(cfg_path), "--once"], cwd=tree, env=env, capture_output=True, text=True, timeout=600)
    (work / "stdout.txt").write_text(p.stdout)
    (work / "stderr.txt").write_text(p.stderr)
    ledger = work / "lane-ledger.jsonl"
    files = sorted(state.rglob("*.jsonl")) + ([ledger] if ledger.exists() else [])
    streams = {str(f.relative_to(work)): [json.loads(x) for x in f.read_text().splitlines() if x.strip()] for f in files}
    written = json.loads(cfg_path.read_text())
    return {"config": written, "config_presend_batch": ("absent" if "presend_batch" not in written else written["presend_batch"]), "rc": p.returncode, "streams": streams, "start_line": (p.stdout.splitlines() or [""])[0], "stderr_tail": p.stderr[-300:]}


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


def count_kind(streams: dict[str, list[Any]], kind: str) -> int:
    return sum(1 for v in streams.values() for r in v if isinstance(r, dict) and r.get("kind") == kind)


def execute(tree_a: Path, tree_b: Path, rc: Path, out: Path, clock: int, balance: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    ra = run_tree(tree_a.resolve(), rc, out, "tree_a", clock, balance)
    rb = run_tree(tree_b.resolve(), rc, out, "tree_b", clock, balance)
    res = compare(ra["streams"], rb["streams"])
    nonempty = sum(len(v) for v in ra["streams"].values()) > 0
    res.update({"clock_ms": clock, "balance_lamports": balance, "rc_a": ra["rc"], "rc_b": rb["rc"], "start_line_a": ra["start_line"], "start_line_b": rb["start_line"],
                "config_presend_batch": ra["config_presend_batch"] if ra["config_presend_batch"] == rb["config_presend_batch"] else "DIFFERS", "nonempty": nonempty,
                "buy_sent_a": count_kind(ra["streams"], "buy_sent")})
    if ra["rc"] or rb["rc"]:
        res["stderr_tail_a"], res["stderr_tail_b"] = ra["stderr_tail"], rb["stderr_tail"]
    res["equal"] = bool(res["equal"] and ra["rc"] == 0 and rb["rc"] == 0 and nonempty)
    return res, ra, rb


def cmd_run(a: argparse.Namespace) -> int:
    out, rc = Path(a.out), Path(a.root_copy)
    clock = a.clock_ms if a.clock_ms is not None else max_row_ms(rc) + 1_000
    res, ra, rb = execute(Path(a.tree_a), Path(a.tree_b), rc, out, clock, a.balance)
    res["schema"] = "floor_exec_equiv_v1"
    if a.require_buy and res["buy_sent_a"] == 0:
        res["equal"], res["require_buy_failed"] = False, True
    print(json.dumps(res, sort_keys=True, indent=1))
    return 0 if res["equal"] else 1


ALLOWED_DROP = ["exec_stage_ms", "stage_ms"]


def load_log_rows(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every row of <root>/decisions/l1-floor-v2/*.jsonl as (file, row), in file order then line order (the chain order: seq ascending)."""
    out = []
    for f in sorted((root / "decisions" / BOT).glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                out.append((f, json.loads(line)))
    return out


def entry_rows(rows: list[tuple[Path, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Entry rows = rows with kind == "entry" and a string order_id, in decision-time order (ms, then seq)."""
    es = [r for _, r in rows if r.get("kind") == "entry" and isinstance(r.get("order_id"), str)]
    return sorted(es, key=lambda r: (int(r.get("ms") or 0), int(r.get("seq") or 0)))


def truncated_root(src: Path, dst: Path, max_seq: int) -> None:
    """A private copy of the decision log holding only rows with seq <= max_seq (a chain prefix; the hash chain still verifies)."""
    if dst.exists():
        shutil.rmtree(dst)
    d = dst / "decisions" / BOT
    d.mkdir(parents=True)
    for f in sorted((src / "decisions" / BOT).glob("*.jsonl")):
        keep = [x for x in f.read_text().splitlines() if x.strip() and int(json.loads(x).get("seq", 0)) <= max_seq]
        if keep:
            (d / f.name).write_text("\n".join(keep) + "\n")


def outcome(streams: dict[str, list[Any]], oid: str) -> str:
    for v in streams.values():
        for r in v:
            if isinstance(r, dict) and r.get("order_id") == oid and r.get("kind") in ("buy_sent", "entry_refused", "buy_port_refused"):
                return "buy_sent" if r["kind"] == "buy_sent" else f"{r['kind']}:{r.get('why') or r.get('label')}"
    return "no_row"


def cmd_per_row(a: argparse.Namespace) -> int:
    out, src = Path(a.out), Path(a.root_copy)
    rows = load_log_rows(src)
    entries = entry_rows(rows)
    runs: list[dict[str, Any]] = []
    bad = False

    def one(label: str, root: Path, clock: int, balance: int, oid: str | None) -> None:
        nonlocal bad
        res, ra, rb = execute(Path(a.tree_a), Path(a.tree_b), root, out / label, clock, balance)
        oa, ob = (outcome(ra["streams"], oid), outcome(rb["streams"], oid)) if oid else (None, None)
        want = ALLOWED_DROP if res["buy_sent_a"] else []
        ok = bool(res["equal"] and ra["rc"] == 0 and rb["rc"] == 0 and res["dropped_keys"] in (want, []) and oa == ob)
        bad = bad or not ok
        runs.append({"run": label, "order_id": oid, "equal": res["equal"], "rc_a": ra["rc"], "rc_b": rb["rc"], "rows_by_kind_a": res["rows_by_kind_a"], "rows_by_kind_b": res["rows_by_kind_b"],
                     "dropped_keys": res["dropped_keys"], "outcome_a": oa, "outcome_b": ob, "clock_ms": clock, "balance_lamports": balance, "ok": ok})

    for n, e in enumerate(entries):
        root = out / "_roots" / f"r{n}"
        truncated_root(src, root, int(e["seq"]))
        one(f"row{n:04d}", root, int(e["ms"]) + 1_000, a.balance, e["order_id"])
    last = max((int(r.get("ms") or 0) for _, r in rows), default=0)
    one("all_rows_floor", src, last + 1_000, a.floor_balance, None)
    print(json.dumps({"schema": "floor_exec_equiv_per_row_v1", "entry_rows": len(entries), "entry_rule": 'kind == "entry" with a str order_id, sorted by (ms, seq)', "all_ok": not bad,
                      "outcomes": dict(sorted(Counter(f"{r['outcome_a']}" for r in runs if r["order_id"]).items())), "runs": runs}, indent=1, sort_keys=True))
    return 1 if bad or not entries else 0


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
    r.add_argument("--require-buy", action="store_true", help="fail if tree A has 0 buy_sent rows")
    q = sub.add_parser("per-row")
    for k in ("--tree-a", "--tree-b", "--root-copy", "--out"):
        q.add_argument(k, required=True)
    q.add_argument("--balance", type=int, default=100_000_000_000, help="per-row runs (default 100 SOL: the wallet floor never binds)")
    q.add_argument("--floor-balance", type=int, default=900_000_000, help="the one all-rows run at the last row's clock (default 0.9 SOL: the wallet floor binds)")
    sub.add_parser("readers")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "readers": cmd_readers, "per-row": cmd_per_row}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
