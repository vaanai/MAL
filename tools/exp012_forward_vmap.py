"""Pool -> V map for the EXP-012 FINAL forward read (DEC-016 Amendment 4 section 3).

Collects EVERY PumpSwap pool printed in the window's trade files (strict=False JSON
parse, venue == "pumpswap", pool is a str), not only mints with a migration row and
not by regex. Only the pool field is ever kept. Subcommands:

  pools     --walk-dir D (--from H --to H | --final-out-dir O) --out pools.json   (writes pools.meta.json)
  fetch     --pools pools.json --vmap MAP --rps R [--new]  (Helius; MAP.reasons.json closed/unreadable)
  snapshot  --vmap MAP --out DIR                                  (read-only copy + snapshots.jsonl)
  validate  --walk-dir D --from H --to H --vmap M --vmap-sha256 S --out validation.json (Amendment 4 s5; never fetches)
  merge     --final MAP --pools pools.json --snapshot SNAP [--snapshot SNAP2] --out OUT

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
CUTOFF = "2026-10-16T00:00:00Z"  # the FINAL fetch must start at or after this; tests patch it, no CLI flag or env var
FINAL_HOURS, FINAL_ROWS_PER_HOUR = 12, 60000  # validate sampling when the window comes from --final-out-dir
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
PENDING_GUARD_LAMPORTS = 10_000_000  # 0.01 SOL: flag pools whose pending counters (A + B) exceed it


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


def side(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def final_window(out_dir: Path) -> tuple[str, str]:
    """(pool_from, to_exclusive) of the last score run in O/runs.jsonl. Counts and hours only."""
    runs = fw.score_runs(fw.read_rows(out_dir / fw.RUNS_NAME))
    if not runs:
        raise Refused(f"no score run in {out_dir / fw.RUNS_NAME}")
    last = runs[-1]
    pf, te = last.get("pool_from"), last.get("to_exclusive")
    if not isinstance(pf, str) or not isinstance(te, str):
        raise Refused(f"last score run in {out_dir / fw.RUNS_NAME} has no pool_from / to_exclusive")
    return pf, te


def cmd_pools(a: argparse.Namespace) -> int:
    if a.final_out_dir:
        if a.from_hour or a.to_hour:
            raise Refused("--final-out-dir takes the window from runs.jsonl; refusing --from/--to alongside it")
        start, end = final_window(Path(a.final_out_dir))
    else:
        if not (a.from_hour and a.to_hour):
            raise Refused("give --final-out-dir, or --from and --to (snapshots before the FINAL exists)")
        start, end = a.from_hour, a.to_hour
    out = Path(a.out)
    meta_path = out.with_name(out.stem + ".meta.json")
    for p in (out, meta_path):
        if p.exists():
            raise Refused(f"{p} exists; refusing to overwrite")
    pools, meta = collect(Path(a.walk_dir), start, end, workers=a.workers)
    meta["window_source"] = "final_out_dir" if a.final_out_dir else "from_to"
    _write_new(out, json.dumps(pools) + "\n")
    _write_new(meta_path, json.dumps(meta, indent=1, sort_keys=True) + "\n")
    print(f"pools: {len(pools)} from {meta['n_files']} files, {meta['n_unparseable_lines']} unparseable lines -> {out}")
    return 0


def fetch_batch_reasons(url: str, pools: list[str]) -> list[tuple[int | None, str | None, dict[str, int | None]]]:
    """getMultipleAccounts. (V, None), or (None, "closed") for a null account, or
    (None, "unreadable") for an account that is present but parse_virtual cannot read; plus {"pending", "v_base"}."""
    import base64
    import time
    import urllib.request

    from tools.pump_history_backfill import redact_rpc_url

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts", "params": [pools, {"encoding": "base64"}]}).encode()
    last: Exception | None = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            resp = json.load(urllib.request.urlopen(req, timeout=60))
            if "error" in resp:
                raise RuntimeError(str(resp["error"])[:200])
            out: list[tuple[int | None, str | None, dict[str, int | None]]] = []
            for acc in resp["result"]["value"]:
                if not acc:
                    out.append((None, "closed", {}))
                    continue
                d = pv.parse_virtual_detail(base64.b64decode(acc["data"][0]))
                out.append((d["v"], None, {"pending": d["pending"], "v_base": d["v_base"]}) if d["v"] is not None else (None, "unreadable", {}))
            return out
        except Exception as exc:  # noqa: BLE001 -- the URL must never leak
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(redact_rpc_url(f"getMultipleAccounts failed: {type(last).__name__}: {last}"))


def load_json_dict(path: Path, what: str) -> dict[str, Any]:
    """A clean Refused for a missing, unreadable, non-JSON or non-object file."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{what} {path} is missing or not valid JSON ({type(exc).__name__})")
    if not isinstance(doc, dict):
        raise Refused(f"{what} {path} is not a JSON object")
    return doc


def load_reasons(map_path: Path) -> dict[str, str]:
    rp = side(map_path, ".reasons.json")
    return load_json_dict(rp, "reasons file") if rp.is_file() else {}


def load_detail(map_path: Path) -> dict[str, dict[str, int | None]]:
    """MAP.detail.json: pool -> {"pending", "v_base"}. A missing or corrupt file is a clean Refused."""
    dp = side(map_path, ".detail.json")
    if not dp.is_file():
        raise Refused(f"detail file {dp} is missing: V alone is not constant (V = V0 - A - B); refetch/snapshot with this tool")
    return load_json_dict(dp, "detail file")


def cmd_fetch(a: argparse.Namespace, fetch: Callable[[list[str]], list[tuple]] | None = None) -> int:
    """`fetch` (tests) returns (V, null_reason) or (V, null_reason, {"pending", "v_base"}) per pool, like fetch_batch_reasons."""
    if a.rps > pv.MAX_RPS:
        raise Refused(f"rps {a.rps} > {pv.MAX_RPS}: walkers share Helius")
    pools = json.loads(Path(a.pools).read_text(encoding="utf-8"))
    if not isinstance(pools, list) or not all(isinstance(p, str) for p in pools):
        raise Refused("pools file is not a list of strings")
    vmap_path = Path(a.vmap)
    sidecars = {k: side(vmap_path, k) for k in (".reasons.json", ".fetch.json", ".null_pools.json", ".detail.json")}
    existing = None
    if a.new:
        for p in (vmap_path, *sidecars.values()):
            if p.exists():
                raise Refused(f"--new: {p} exists; refusing to reuse or overwrite")
    elif vmap_path.is_file():
        existing = pv.load_map(vmap_path)
        print(f"WARNING: reusing {sum(v is not None for v in existing.values())} existing non-null values from {vmap_path}; only nulls and new pools are fetched", file=sys.stderr)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reasons = {} if a.new else load_reasons(vmap_path)
    detail: dict[str, dict[str, int | None]] = {} if (a.new or not sidecars[".detail.json"].is_file()) else load_detail(vmap_path)
    if fetch is None:
        url = pv._rpc_url()
        fetch = lambda chunk: fetch_batch_reasons(url, chunk)  # noqa: E731
    got_reason: dict[str, str | None] = {}
    got_detail: dict[str, dict[str, int | None]] = {}

    def wrapped(chunk: list[str]) -> list[int | None]:
        res = fetch(chunk)
        for p, r in zip(chunk, res):
            got_reason[p] = r[1]
            if r[0] is not None and len(r) > 2:
                got_detail[p] = {"pending": r[2].get("pending"), "v_base": r[2].get("v_base")}
            else:
                got_detail.setdefault(p, {})
        return [r[0] for r in res]

    vmap, calls = pv.build_map(pools, existing=existing, fetch=wrapped, rps=a.rps, log=lambda m: print(m, file=sys.stderr))
    for p, r in got_reason.items():
        if r is None:
            reasons.pop(p, None)
        else:
            reasons[p] = r
    for p, d in got_detail.items():
        if d:
            detail[p] = d
        else:
            detail.pop(p, None)
    pv.save_map(vmap_path, vmap, calls)
    sidecars[".detail.json"].write_text(json.dumps(dict(sorted(detail.items()))) + "\n", encoding="utf-8")
    sidecars[".reasons.json"].write_text(json.dumps(dict(sorted(reasons.items()))) + "\n", encoding="utf-8")
    fj = sidecars[".fetch.json"]
    prior = json.loads(fj.read_text()) if fj.is_file() else {}
    doc = {**prior, "fetch_started_utc": prior.get("fetch_started_utc", started), "new": prior.get("new", bool(a.new)), "last_fetch_utc": started, "n_pools_requested": len(pools), "pools_sha256": sha256_file(Path(a.pools)), "detail_sha256": sha256_file(sidecars[".detail.json"])}
    fj.write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")
    nulls = sorted(p for p in pools if vmap.get(p) is None)
    sidecars[".null_pools.json"].write_text(json.dumps(nulls) + "\n", encoding="utf-8")  # ids stay in a file, not in logs
    n_unread = sum(1 for p in nulls if reasons.get(p) == "unreadable")
    print(f"fetch: n={len(vmap)} n_null={len(nulls)} n_unreadable={n_unread} calls={calls} -> {vmap_path}")
    return 0


def cmd_snapshot(a: argparse.Namespace, now: datetime | None = None) -> int:
    src, outdir = Path(a.vmap), Path(a.out)
    utc = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    dest = outdir / f"vmap-snapshot-{utc}.json"
    if dest.exists():
        raise Refused(f"{dest} exists; refusing to overwrite")
    src_detail = side(src, ".detail.json")
    if not src_detail.is_file():
        raise Refused(f"{src_detail} missing: snapshot the map from a fetch made with this tool")
    dest_detail = side(dest, ".detail.json")
    if dest_detail.exists():
        raise Refused(f"{dest_detail} exists; refusing to overwrite")
    _write_new(dest, src.read_text(encoding="utf-8"), readonly=True)
    _write_new(dest_detail, src_detail.read_text(encoding="utf-8"), readonly=True)
    vmap = pv.load_map(dest)  # counts come from the copy, not the source
    rec = {"utc": utc, "file": dest.name, "sha256": sha256_file(dest), "detail_sha256": sha256_file(dest_detail), "n": len(vmap), "n_null": sum(v is None for v in vmap.values())}
    with (outdir / LEDGER_NAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    print(f"snapshot: {dest} n={rec['n']} n_null={rec['n_null']} sha256={rec['sha256']}")
    return 0


def check_in_ledger(snap: Path) -> None:
    ledger = snap.parent / LEDGER_NAME
    if not ledger.is_file():
        raise Refused(f"{snap}: no {LEDGER_NAME} next to it")
    want = sha256_file(snap)
    dsnap = side(snap, ".detail.json")
    if not dsnap.is_file():
        raise Refused(f"{dsnap} missing: every snapshot needs its detail sidecar")
    want_d = sha256_file(dsnap)
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("file") == snap.name and r.get("sha256") == want and r.get("detail_sha256") == want_d:
            return
    raise Refused(f"{snap} is not in {ledger} with a matching sha256 and detail_sha256")


def _b(detail: dict[str, Any], p: str) -> int | None:
    d = detail.get(p)
    b = d.get("v_base") if isinstance(d, dict) else None
    return b if isinstance(b, int) and not isinstance(b, bool) else None


def _same_base(p: str, b1: int | None, b2: int | None, what: str) -> None:
    if b1 is None or b2 is None:
        raise Refused(f"{what}: no v_base for {p}; constancy cannot be checked")
    if b1 != b2:
        raise Refused(f"{what}: v_base for {p} differs ({b1} vs {b2}); V0 is constant per pool")


def merge_maps(final: dict[str, int | None], snaps: Sequence[dict[str, int | None]], pool_set: set[str], reasons: dict[str, str] | None = None, final_detail: dict[str, Any] | None = None, snap_details: Sequence[dict[str, Any]] | None = None) -> tuple[dict[str, int | None], list[str], list[str], dict[str, Any]]:
    """(merged, filled, ignored_outside_set, discrepancies).

    The post-cutoff map is authoritative for every pool it prices (non-null). Snapshots only FILL pools of the set that
    are closed-null or absent in it, copying the v of the LAST snapshot given (list them oldest first).

    Constancy (DEC-016 Am.5 §3, 2026-10-06 edit after job #278): v_base = V + A + B was found to move on a few pools
    (6 of 52,643 in ~6 h), so it is NOT checked for pools the post-cutoff map prices; those differences are reported
    only (`discrepancies`). It IS checked for every pool that is filled: if two snapshots carry that pool, their v_base
    must be equal and present, else refuse (a filled value must not be ambiguous). A null with reason "unreadable" or
    with no reason refuses, as before."""
    reasons = reasons or {}
    final_detail = final_detail or {}
    snap_details = snap_details if snap_details is not None else [{} for _ in snaps]
    snap_v: dict[str, int] = {}
    snap_bases: dict[str, list[int | None]] = {}
    for sm, sd in zip(snaps, snap_details):
        for p, v in sm.items():
            if v is None:
                continue
            snap_v[p] = v
            snap_bases.setdefault(p, []).append(_b(sd, p))
    out = dict(final)
    want = sorted(p for p in snap_v if out.get(p) is None)
    ignored = [p for p in want if p not in pool_set]
    filled = [p for p in want if p in pool_set]
    no_reason = sorted(p for p in pool_set if p in final and final[p] is None and reasons.get(p) not in ("closed", "unreadable"))
    if no_reason:
        raise Refused(f"{len(no_reason)} pool(s) in the set are null in the final map with no recorded reason: never filled; refetch with this tool")
    bad = [p for p in filled if p in final and reasons.get(p) == "unreadable"]
    if bad:
        raise Refused(f"{len(bad)} pool(s) are null in the final map as unreadable but non-null in a snapshot: not filled; resolve first")
    for p in filled:
        bases = snap_bases[p]
        for b in bases[1:]:
            _same_base(p, bases[0], b, "snapshots disagree on a pool being filled")
    final_vs_snap: list[list[Any]] = []
    snap_vs_snap: list[str] = []
    n_out_of_set = 0
    n_uncomparable = 0
    filled_set = set(filled)
    for p, bases in snap_bases.items():
        if p in filled_set:
            continue
        if p not in pool_set:
            n_out_of_set += 1
            continue
        known = [b for b in bases if b is not None]
        if len(set(known)) > 1:
            snap_vs_snap.append(p)
        if final.get(p) is not None:
            fb = _b(final_detail, p)
            if fb is None or not known or len(known) < len(bases):
                n_uncomparable += 1
            if fb is not None and known and fb != known[-1]:
                final_vs_snap.append([p, known[-1], fb])
    final_vs_snap.sort()
    disc = {
        "n_final_vs_snapshot": len(final_vs_snap),
        "final_vs_snapshot": final_vs_snap,
        "max_abs_final_vs_snapshot": max((abs(b - a) for _, a, b in final_vs_snap), default=0),
        "n_snapshot_vs_snapshot": len(snap_vs_snap),
        "snapshot_vs_snapshot": sorted(snap_vs_snap),
        "n_uncomparable": n_uncomparable,
        "n_snapshot_pools_outside_set": n_out_of_set,
        "note": "pools of the §1 set only. The post-cutoff map prices these pools; vbook treats final_vs_snapshot pools like null-V pools under Amendment 4 §3 (DEC-016 Am.5 §3, 2026-10-06 edit)",
    }
    for p in filled:
        out[p] = snap_v[p]
    return out, filled, ignored, disc


def final_fetch_block(final_path: Path, pools_path: Path) -> dict[str, Any]:
    """MAP.fetch.json must say this map came from a fresh `fetch --new` that started at or after CUTOFF."""
    fj = side(final_path, ".fetch.json")
    if not fj.is_file():
        raise Refused(f"{fj} missing: the FINAL map must come from `fetch --new`")
    doc = load_json_dict(fj, "fetch file")
    started = doc.get("fetch_started_utc")
    if doc.get("new") is not True:
        raise Refused(f"{fj}: new is not true; the FINAL map must come from `fetch --new`")
    try:
        t0 = datetime.strptime(started, TS_FMT)
    except (TypeError, ValueError):
        raise Refused(f"{fj}: fetch_started_utc is missing or malformed")
    if t0 < datetime.strptime(CUTOFF, TS_FMT):
        raise Refused(f"{fj}: fetch_started_utc {started} is before the cutoff {CUTOFF}")
    want = sha256_file(pools_path)
    if doc.get("pools_sha256") != want:
        raise Refused(f"{fj}: pools_sha256 {doc.get('pools_sha256')} != sha256 of --pools {want}: the whole set must go through the post-cutoff fetch")
    return {"new": True, "fetch_started_utc": started, "cutoff": CUTOFF, "pools_sha256": want, "detail_sha256": doc.get("detail_sha256")}


def cmd_merge(a: argparse.Namespace) -> int:
    out = Path(a.out)
    meta_path = side(out, ".merge.json")
    for p in (out, meta_path):
        if p.exists():
            raise Refused(f"{p} exists; refusing to overwrite")
    final_path = Path(a.final)
    snap_paths = [Path(s) for s in a.snapshot]
    final_fetch = final_fetch_block(final_path, Path(a.pools))
    snap_paths = sorted(snap_paths, key=lambda x: x.name)  # oldest first (UTC in the name); a fill copies the latest
    for sp in snap_paths:
        check_in_ledger(sp)
    pool_set = set(json.loads(Path(a.pools).read_text(encoding="utf-8")))
    if not side(final_path, ".reasons.json").is_file():
        raise Refused(f"{side(final_path, '.reasons.json')} missing: run the final fetch with this tool so null reasons are recorded")
    reasons = load_reasons(final_path)
    final = pv.load_map(final_path)
    final_detail = load_detail(final_path)
    if final_fetch["detail_sha256"] != sha256_file(side(final_path, ".detail.json")):
        raise Refused(f"{side(final_path, '.detail.json')} does not match detail_sha256 in the fetch file")
    snap_details = [load_detail(sp) for sp in snap_paths]
    merged, filled, ignored, disc = merge_maps(final, [pv.load_map(s) for s in snap_paths], pool_set, reasons, final_detail, snap_details)
    pending_after: dict[str, int] = {}
    for p in pool_set:
        d = final_detail.get(p) if merged.get(p) is not None and p in final and final[p] is not None else None
        if d is None and p in filled:
            d = next((sd[p] for sd in reversed(snap_details) if p in sd), None)
        if isinstance(d, dict) and isinstance(d.get("pending"), int):
            pending_after[p] = d["pending"]
    n_pending = sum(1 for v in pending_after.values() if v > PENDING_GUARD_LAMPORTS)
    pv.save_map(out, merged, 0)
    os.chmod(out, 0o444)
    unreadable = sorted(p for p in pool_set if p in merged and merged[p] is None and reasons.get(p) == "unreadable")
    meta = {
        "final_fetch": final_fetch,
        "n": len(merged),
        "n_pools_set": len(pool_set),
        "n_null_before": sum(1 for p in pool_set if p in final and final[p] is None),
        "n_absent_before": sum(p not in final for p in pool_set),
        "n_filled_from_snapshot": len(filled),
        "filled_pools": filled,
        "n_ignored_outside_set": len(ignored),
        "ignored_pools": ignored,
        "n_null_after": sum(1 for p in pool_set if p in merged and merged[p] is None),
        "n_absent_after": sum(p not in merged for p in pool_set),
        "n_pending_gt_0.01SOL": n_pending,
        "n_with_pending_detail": len(pending_after),
        "n_unreadable": len(unreadable),
        "v_base_discrepancies": disc,
        "unreadable_pools": unreadable,
        "sha256": {"final": sha256_file(final_path), "pools": sha256_file(Path(a.pools)), "reasons": sha256_file(side(final_path, ".reasons.json")), "final_detail": sha256_file(side(final_path, ".detail.json")), "snapshot_details": [sha256_file(side(sp, ".detail.json")) for sp in snap_paths], "fetch": sha256_file(side(final_path, ".fetch.json")), "snapshots": [sha256_file(s) for s in snap_paths], "out": sha256_file(out)},
    }
    _write_new(meta_path, json.dumps(meta, indent=1, sort_keys=True) + "\n", readonly=True)
    print(f"merge: n={meta['n']} v_base_discrepancies={disc['n_final_vs_snapshot']} filled={len(filled)} ignored={len(ignored)} n_null_after={meta['n_null_after']} n_absent_after={meta['n_absent_after']} n_unreadable={len(unreadable)} -> {out}")
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
    if a.final_out_dir:
        if a.from_hour or a.to_hour:
            raise Refused("--final-out-dir takes the window from runs.jsonl; refusing --from/--to alongside it")
        if a.hours is not None or a.rows_per_hour is not None:
            raise Refused(f"--final-out-dir fixes the sample at {FINAL_HOURS} hours and {FINAL_ROWS_PER_HOUR} rows per hour; refusing --hours/--rows-per-hour")
        start, end = final_window(Path(a.final_out_dir))
        n_hours, rows_per_hour, source = FINAL_HOURS, FINAL_ROWS_PER_HOUR, "final_out_dir"
    else:
        if not (a.from_hour and a.to_hour):
            raise Refused("give --final-out-dir, or --from and --to (pre-read dry run)")
        start, end, source = a.from_hour, a.to_hour, "manual"
        n_hours = FINAL_HOURS if a.hours is None else a.hours
        rows_per_hour = FINAL_ROWS_PER_HOUR if a.rows_per_hour is None else a.rows_per_hour
    doc = validate_window(Path(a.walk_dir), start, end, Path(a.vmap), a.vmap_sha256, n_hours, rows_per_hour, a.workers)
    doc["window_source"] = source
    _write_new(out, json.dumps(doc, indent=1, sort_keys=True) + "\n")
    v = doc["verdict"]
    print(f"validate: rows={doc['n_rows']} pools={doc['n_pools']} absent={doc['n_pools_absent_from_map']} sell_ok={v['sell_ok']} buy_ok={v['buy_ok']} ok={v['ok']} -> {out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pools")
    p.add_argument("--walk-dir", required=True)
    p.add_argument("--from", dest="from_hour")
    p.add_argument("--to", dest="to_hour")
    p.add_argument("--final-out-dir", help="forward OUT dir: window = last score run's pool_from .. to_exclusive in runs.jsonl")
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=8)
    p.set_defaults(fn=cmd_pools)
    p = sub.add_parser("fetch")
    p.add_argument("--pools", required=True)
    p.add_argument("--vmap", required=True)
    p.add_argument("--rps", type=float, default=pv.MAX_RPS)
    p.add_argument("--new", action="store_true", help="refuse if the map exists; the FINAL fetch always uses this")
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("snapshot")
    p.add_argument("--vmap", required=True)
    p.add_argument("--out", required=True, help="directory; file is vmap-snapshot-<UTC>.json, ledger is snapshots.jsonl")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("merge")
    p.add_argument("--final", required=True)
    p.add_argument("--pools", required=True, help="the section 1 pool set (pools.json)")
    p.add_argument("--snapshot", action="append", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("validate")
    p.add_argument("--walk-dir", required=True)
    p.add_argument("--from", dest="from_hour")
    p.add_argument("--to", dest="to_hour")
    p.add_argument("--final-out-dir", help="forward OUT dir: window from runs.jsonl; sample fixed at 12 hours x 60000 rows")
    p.add_argument("--vmap", required=True)
    p.add_argument("--vmap-sha256", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--hours", type=int, default=None, help=f"manual windows only (default {FINAL_HOURS})")
    p.add_argument("--rows-per-hour", type=int, default=None, help=f"manual windows only (default {FINAL_ROWS_PER_HOUR})")
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
