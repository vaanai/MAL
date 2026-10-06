"""Pool -> V map for the EXP-012 FINAL forward read (DEC-016 Amendment 4 section 3).

Collects EVERY PumpSwap pool printed in the window's trade files (strict=False JSON
parse, venue == "pumpswap", pool is a str), not only mints with a migration row and
not by regex. Only the pool field is ever kept. Subcommands:

  pools     --walk-dir D --from HOUR --to HOUR --out pools.json   (writes pools.meta.json)
  fetch     --pools pools.json --vmap MAP --rps R                 (Helius via pumpswap_virtual)
  snapshot  --vmap MAP --out DIR                                  (read-only copy + snapshots.jsonl)
  validate  --walk-dir D --from H --to H --vmap M --vmap-sha256 S --out validation.json (Amendment 4 s5; never fetches)
  merge     --final MAP --snapshot SNAP [--snapshot SNAP2] --out OUT

V is constant per pool and a closed pool reads null later, so an early snapshot fills
pools that are null (or absent) after the cutoff. Disagreeing non-null values refuse.
Run: PYTHONPATH=. python -m tools.exp012_forward_vmap <cmd> ...
"""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing as mp
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import tools.exp012_forward as fw
import tools.pumpswap_virtual as pv
from tools.latency_curve import _hour_file

LEDGER_NAME = "snapshots.jsonl"


class Refused(Exception):
    pass


def hours_in(start: str, end: str) -> list[str]:
    s, e = fw.hour_dt(start), fw.hour_dt(end)
    out: list[str] = []
    while s < e:
        out.append(fw.hour_key(s))
        s += timedelta(hours=1)
    return out


def sha256_file(path: Path) -> str:
    return fw._sha256_file(path)


def _open_lines(path: Path) -> Iterable[bytes]:
    if path.name.endswith(".zst"):
        p = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE)
        assert p.stdout is not None
        try:
            yield from p.stdout
        finally:
            p.stdout.close()
            if p.wait() != 0:
                raise RuntimeError(f"zstdcat failed on {path}")
    elif path.name.endswith(".gz"):
        with gzip.open(path, "rb") as fh:
            yield from fh
    else:
        with path.open("rb") as fh:
            yield from fh


def pool_of_line(line: bytes | str) -> tuple[str | None, bool]:
    """(pool or None, unparseable). Cheap prefilter, then a real parse; nothing else is kept."""
    if isinstance(line, bytes):
        if b"pumpswap" not in line:
            return None, False
        text = line.decode("utf-8", errors="replace")
    else:
        if "pumpswap" not in line:
            return None, False
        text = line
    try:
        rec = json.loads(text, strict=False)
    except (ValueError, RecursionError):
        return None, True
    if not isinstance(rec, dict):
        return None, True
    pool = rec.get("pool")
    if rec.get("venue") == "pumpswap" and isinstance(pool, str) and pool:
        return pool, False
    return None, False


def _scan_file(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    pools: set[str] = set()
    bad = 0
    for line in _open_lines(path):
        p, unparseable = pool_of_line(line)
        bad += unparseable
        if p:
            pools.add(p)
    return {"path": path_str, "sha256": sha256_file(path), "pools": sorted(pools), "unparseable": bad}


def collect(walk_dir: Path, start: str, end: str, workers: int = 8) -> tuple[list[str], dict[str, Any]]:
    hours = hours_in(start, end)
    if not hours:
        raise Refused(f"empty window [{start}, {end})")
    problems = fw.hour_problems(walk_dir, hours)  # sealed + verified OK + file bytes match the verify sha256
    if problems:
        raise Refused("; ".join(problems))
    ok = fw.verified_hours(walk_dir)
    files: list[Path] = []
    for h in hours:
        f = _hour_file(walk_dir / "trades", "trades", h)
        if f is None:  # hour_problems already refuses this; belt and braces
            raise Refused(f"hour {h}: no trades file")
        files.append(f)
    if workers <= 1:
        results = [_scan_file(str(f)) for f in files]
    else:
        with mp.get_context("spawn").Pool(processes=min(workers, len(files))) as pool:
            results = pool.map(_scan_file, [str(f) for f in files])
    for h, r in zip(hours, results):  # bytes read must be the bytes that were verified
        if r["sha256"] != ok[h]["sha256"].get("trades"):
            raise Refused(f"hour {h}: trades sha256 changed while reading")
    allpools: set[str] = set()
    for r in results:
        allpools.update(r["pools"])
    meta = {
        "walk_dir": str(walk_dir),
        "from": start,
        "to": end,
        "hours": hours,
        "n_files": len(files),
        "n_pools": len(allpools),
        "n_unparseable_lines": sum(r["unparseable"] for r in results),
        "trade_file_sha256": {Path(r["path"]).name: r["sha256"] for r in results},
    }
    return sorted(allpools), meta


def _write_new(path: Path, text: str, readonly: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444 if readonly else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def cmd_pools(a: argparse.Namespace) -> int:
    out = Path(a.out)
    meta_path = out.with_name(out.stem + ".meta.json")
    for p in (out, meta_path):
        if p.exists():
            raise Refused(f"{p} exists; refusing to overwrite")
    pools, meta = collect(Path(a.walk_dir), a.from_hour, a.to_hour, workers=a.workers)
    _write_new(out, json.dumps(pools) + "\n")
    _write_new(meta_path, json.dumps(meta, indent=1, sort_keys=True) + "\n")
    print(f"pools: {len(pools)} from {meta['n_files']} files, {meta['n_unparseable_lines']} unparseable lines -> {out}")
    return 0


def cmd_fetch(a: argparse.Namespace, fetch: Callable[[list[str]], list[int | None]] | None = None) -> int:
    if a.rps > pv.MAX_RPS:
        raise Refused(f"rps {a.rps} > {pv.MAX_RPS}: walkers share Helius")
    pools = json.loads(Path(a.pools).read_text(encoding="utf-8"))
    if not isinstance(pools, list) or not all(isinstance(p, str) for p in pools):
        raise Refused("pools file is not a list of strings")
    vmap_path = Path(a.vmap)
    existing = pv.load_map(vmap_path) if vmap_path.is_file() else None
    vmap, calls = pv.build_map(pools, existing=existing, fetch=fetch, rps=a.rps, log=lambda m: print(m, file=sys.stderr))
    pv.save_map(vmap_path, vmap, calls)
    nulls = sorted(p for p in pools if vmap.get(p) is None)
    print(f"fetch: n={len(vmap)} null={len(nulls)} calls={calls} -> {vmap_path}")
    for p in nulls:
        print(f"null {p}")
    return 0


def cmd_snapshot(a: argparse.Namespace, now: datetime | None = None) -> int:
    src, outdir = Path(a.vmap), Path(a.out)
    vmap = pv.load_map(src)
    utc = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    dest = outdir / f"vmap-snapshot-{utc}.json"
    if dest.exists():
        raise Refused(f"{dest} exists; refusing to overwrite")
    _write_new(dest, src.read_text(encoding="utf-8"), readonly=True)
    rec = {"utc": utc, "file": dest.name, "sha256": sha256_file(dest), "n": len(vmap), "n_null": sum(v is None for v in vmap.values())}
    with (outdir / LEDGER_NAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    print(f"snapshot: {dest} n={rec['n']} n_null={rec['n_null']} sha256={rec['sha256']}")
    return 0


def merge_maps(final: dict[str, int | None], snaps: Sequence[dict[str, int | None]]) -> tuple[dict[str, int | None], list[str]]:
    snap_v: dict[str, int] = {}
    for s in snaps:
        for p, v in s.items():
            if v is None:
                continue
            if p in snap_v and snap_v[p] != v:
                raise Refused(f"snapshots disagree on {p}: {snap_v[p]} vs {v} (V is constant per pool)")
            snap_v[p] = v
    for p, v in final.items():
        if v is not None and p in snap_v and snap_v[p] != v:
            raise Refused(f"post-cutoff V for {p} is {v}, snapshot has {snap_v[p]}")
    out = dict(final)
    filled = sorted(p for p, v in snap_v.items() if out.get(p) is None)
    for p in filled:
        out[p] = snap_v[p]
    return out, filled


def cmd_merge(a: argparse.Namespace) -> int:
    out = Path(a.out)
    meta_path = out.with_name(out.name + ".merge.json")
    for p in (out, meta_path):
        if p.exists():
            raise Refused(f"{p} exists; refusing to overwrite")
    final_path = Path(a.final)
    snap_paths = [Path(s) for s in a.snapshot]
    final = pv.load_map(final_path)
    merged, filled = merge_maps(final, [pv.load_map(s) for s in snap_paths])
    pv.save_map(out, merged, 0)
    os.chmod(out, 0o444)
    meta = {
        "n": len(merged),
        "n_null_before": sum(v is None for v in final.values()),
        "n_filled_from_snapshot": len(filled),
        "filled_pools": filled,
        "n_null_after": sum(v is None for v in merged.values()),
        "sha256": {"final": sha256_file(final_path), "snapshots": [sha256_file(s) for s in snap_paths], "out": sha256_file(out)},
    }
    _write_new(meta_path, json.dumps(meta, indent=1, sort_keys=True) + "\n", readonly=True)
    print(f"merge: n={meta['n']} null_before={meta['n_null_before']} filled={len(filled)} null_after={meta['n_null_after']} -> {out}")
    return 0


def sample_hours(hours: Sequence[str], n: int) -> list[str]:
    """n hours spread evenly over the window (all of them if n >= len)."""
    if n <= 0:
        raise Refused("--hours must be positive")
    if n >= len(hours):
        return list(hours)
    return [hours[(2 * i + 1) * len(hours) // (2 * n)] for i in range(n)]


def _sample_rows(args: tuple[str, int]) -> dict[str, Any]:
    path_str, cap = args
    path = Path(path_str)
    rows: list[dict[str, Any]] = []
    bad = 0
    for line in _open_lines(path):
        if len(rows) >= cap:
            break
        if b"pumpswap" not in line:
            continue
        try:
            rec = json.loads(line.decode("utf-8", errors="replace"), strict=False)
        except (ValueError, RecursionError):
            bad += 1
            continue
        if isinstance(rec, dict) and rec.get("venue") == "pumpswap":
            rows.append(rec)
    return {"path": path_str, "rows": rows, "unparseable": bad, "sha256": sha256_file(path)}


def verdict(res: dict[str, Any]) -> dict[str, Any]:
    """DEC-016 Amendment 4 section 5, read off validate_rows' output.

    sell: dist_bps.v_pos.sell_pre_V (V-corrected, the row's own pre-trade reserves). validate_rows gives the
      SIGNED median, not the median of |error|, so that number is derived here: median|e| < 1 bps holds when
      more than half the errors have |e| < 1 (share_abs_lt_1bps > 0.5). The 75% share test implies it.
    buy: dist_bps.v_pos.buy_fee_resid_V share_abs_lt_1bps (implied-fee residual to the nearest convention).
    An empty group (n == 0) is not ok."""
    d = res["dist_bps"]["v_pos"]
    sell, buy = d["sell_pre_V"], d["buy_fee_resid_V"]
    sell_share = sell.get("share_abs_lt_1bps")
    buy_share = buy.get("share_abs_lt_1bps")
    median_abs_lt_1 = sell_share is not None and sell_share > 0.5
    sell_ok = bool(median_abs_lt_1 and sell_share >= 0.75)
    buy_ok = bool(buy_share is not None and buy_share >= 0.90)
    return {
        "sell_n": sell.get("n", 0), "sell_share_within_1bps": sell_share, "sell_median_abs_lt_1bps": median_abs_lt_1,
        "buy_n": buy.get("n", 0), "buy_share_within_1bps": buy_share,
        "sell_ok": sell_ok, "buy_ok": buy_ok, "ok": sell_ok and buy_ok,
        "source_keys": {"sell": "dist_bps.v_pos.sell_pre_V", "buy": "dist_bps.v_pos.buy_fee_resid_V"},
    }


def validate_window(walk_dir: Path, start: str, end: str, vmap_path: Path, vmap_sha: str, n_hours: int = 12, rows_per_hour: int = 60000, workers: int = 8) -> dict[str, Any]:
    from tools.exp012_virtual_rescore import validate_rows

    if sha256_file(vmap_path) != vmap_sha:
        raise Refused(f"vmap sha256 {sha256_file(vmap_path)} != --vmap-sha256 {vmap_sha}")
    hours = hours_in(start, end)
    if not hours:
        raise Refused(f"empty window [{start}, {end})")
    problems = fw.hour_problems(walk_dir, hours)
    if problems:
        raise Refused("; ".join(problems))
    ok = fw.verified_hours(walk_dir)
    used = sample_hours(hours, n_hours)
    files = []
    for h in used:
        f = _hour_file(walk_dir / "trades", "trades", h)
        if f is None:
            raise Refused(f"hour {h}: no trades file")
        files.append(f)
    jobs = [(str(f), rows_per_hour) for f in files]
    if workers <= 1:
        results = [_sample_rows(j) for j in jobs]
    else:
        with mp.get_context("spawn").Pool(processes=min(workers, len(jobs))) as pool:
            results = pool.map(_sample_rows, jobs)
    rows: list[dict[str, Any]] = []
    for h, r in zip(used, results):
        if r["sha256"] != ok[h]["sha256"].get("trades"):
            raise Refused(f"hour {h}: trades sha256 changed while reading")
        rows.extend(r["rows"])
    vmap = pv.load_map(vmap_path)  # never fetched: an absent pool is counted and skipped
    pools = {r["pool"] for r in rows if isinstance(r.get("pool"), str)}
    res = validate_rows(rows, vmap)
    return {
        "result": res,
        "verdict": verdict(res),
        "hours_used": used,
        "window": {"from": start, "to": end, "n_hours": len(hours)},
        "rows_per_hour_cap": rows_per_hour,
        "n_rows": len(rows),
        "n_pools": len(pools),
        "n_pools_absent_from_map": sum(1 for p in pools if p not in vmap),
        "n_unparseable_lines": sum(r["unparseable"] for r in results),
        "vmap_sha256": vmap_sha,
        "trade_file_sha256": {Path(r["path"]).name: r["sha256"] for r in results},
    }


def cmd_validate(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if out.exists():
        raise Refused(f"{out} exists; refusing to overwrite")
    doc = validate_window(Path(a.walk_dir), a.from_hour, a.to_hour, Path(a.vmap), a.vmap_sha256, a.hours, a.rows_per_hour, a.workers)
    _write_new(out, json.dumps(doc, indent=1, sort_keys=True) + "\n")
    v = doc["verdict"]
    print(f"validate: rows={doc['n_rows']} pools={doc['n_pools']} absent={doc['n_pools_absent_from_map']} sell_ok={v['sell_ok']} buy_ok={v['buy_ok']} ok={v['ok']} -> {out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pools")
    p.add_argument("--walk-dir", required=True)
    p.add_argument("--from", dest="from_hour", required=True)
    p.add_argument("--to", dest="to_hour", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=8)
    p.set_defaults(fn=cmd_pools)
    p = sub.add_parser("fetch")
    p.add_argument("--pools", required=True)
    p.add_argument("--vmap", required=True)
    p.add_argument("--rps", type=float, default=pv.MAX_RPS)
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("snapshot")
    p.add_argument("--vmap", required=True)
    p.add_argument("--out", required=True, help="directory; file is vmap-snapshot-<UTC>.json, ledger is snapshots.jsonl")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("merge")
    p.add_argument("--final", required=True)
    p.add_argument("--snapshot", action="append", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("validate")
    p.add_argument("--walk-dir", required=True)
    p.add_argument("--from", dest="from_hour", required=True)
    p.add_argument("--to", dest="to_hour", required=True)
    p.add_argument("--vmap", required=True)
    p.add_argument("--vmap-sha256", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--hours", type=int, default=12)
    p.add_argument("--rows-per-hour", type=int, default=60000)
    p.add_argument("--workers", type=int, default=8)
    p.set_defaults(fn=cmd_validate)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
