"""Pool -> V map for the EXP-012 FINAL forward read (DEC-016 Amendment 4 section 3).

Collects EVERY PumpSwap pool printed in the window's trade files (strict=False JSON
parse, venue == "pumpswap", pool is a str), not only mints with a migration row and
not by regex. Only the pool field is ever kept. Subcommands:

  pools     --walk-dir D (--from H --to H | --final-out-dir O) --out pools.json   (writes pools.meta.json)
  fetch     --pools pools.json --vmap MAP --rps R [--new]  (Helius; MAP.reasons.json closed/unreadable)
  snapshot  --vmap MAP --out DIR                                  (read-only copy + snapshots.jsonl)
  validate  --walk-dir D --from H --to H --vmap M --vmap-sha256 S --out validation.json (Amendment 4 s5; never fetches)
  diffs     --a DETAIL --b DETAIL --pools pools.json --out OUT.json   (pools whose V0 differs; ids + both values)
  lphist    --pools P.json --from ISO --to ISO --out OUT.json [--rps 5]  (LP Deposit/Withdraw events; read-only file + .meta.json)
  merge     --final MAP --pools pools.json --snapshot SNAP [--snapshot SNAP2] [--lphist FILE ...] --out OUT

V0 (= V + A + B) is constant per pool except at LP Deposit/Withdraw, where it becomes floor(V0 * S_after / S_before)
(DEC-016 Amendment 5 section 7). A closed pool reads null later, so an early snapshot fills pools that are null (or
absent) after the cutoff. Two differing V0 values must be explained by the pool's LP events (--lphist) or the pool is
unexplained / unresolved: listed in side files, never filled, and capped at 0.1% of the pool set.
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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import tools.exp012_forward as fw
import tools.pumpswap_lp_history as lph
import tools.pumpswap_virtual as pv
from tools.latency_curve import _hour_file

LEDGER_NAME = "snapshots.jsonl"
CUTOFF = "2026-10-16T00:00:00Z"  # the FINAL fetch must start at or after this; tests patch it, no CLI flag or env var
FINAL_HOURS, FINAL_ROWS_PER_HOUR = 12, 60000  # validate sampling when the window comes from --final-out-dir
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
DRYRUN_SUFFIX = ".dryrun.json"
MOVED_CEILING = 0.001  # unexplained + unresolved pools may not exceed this share of the pool set
OLD_FETCH_MAX_MIN = 30  # ASSUMPTION: a fetch record without fetch_ended_utc ended no later than last_fetch_utc (a START time) + this
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


def lp_supply_of(raw: bytes) -> int | None:
    """LP mint supply stored in the pool account (parse_pool_account's lp_supply), or None if the account is too short."""
    from tools import pumpswap_tx as tx

    try:
        v = tx.parse_pool_account(raw).get("lp_supply")
    except ValueError:
        return None
    return v if isinstance(v, int) else None


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
            slot = (resp["result"].get("context") or {}).get("slot")
            for acc in resp["result"]["value"]:
                if not acc:
                    out.append((None, "closed", {"slot": slot}))
                    continue
                raw = base64.b64decode(acc["data"][0])
                d = pv.parse_virtual_detail(raw)
                out.append((d["v"], None, {"pending": d["pending"], "v_base": d["v_base"], "lp_supply": lp_supply_of(raw), "slot": slot}) if d["v"] is not None else (None, "unreadable", {"slot": slot}))
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
    slots: list[int] = []

    def wrapped(chunk: list[str]) -> list[int | None]:
        res = fetch(chunk)
        for p, r in zip(chunk, res):
            got_reason[p] = r[1]
            if len(r) > 2 and isinstance(r[2].get("slot"), int):
                slots.append(r[2]["slot"])
            if r[0] is not None and len(r) > 2:
                got_detail[p] = {"pending": r[2].get("pending"), "v_base": r[2].get("v_base")}
                if "lp_supply" in r[2]:  # no key = None: old detail files and fakes that do not give it
                    got_detail[p]["lp_supply"] = r[2]["lp_supply"]
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
    ended = datetime.now(timezone.utc).strftime(TS_FMT)
    smin = min([x for x in (prior.get("fetch_slot_min"), *slots) if isinstance(x, int)], default=None)
    smax = max([x for x in (prior.get("fetch_slot_max"), *slots) if isinstance(x, int)], default=None)
    doc = {**prior, "fetch_started_utc": prior.get("fetch_started_utc", started), "new": prior.get("new", bool(a.new)), "last_fetch_utc": started, "fetch_ended_utc": ended, "fetch_slot_min": smin, "fetch_slot_max": smax, "n_pools_requested": len(pools), "pools_sha256": sha256_file(Path(a.pools)), "detail_sha256": sha256_file(sidecars[".detail.json"])}
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
    src_fetch0 = side(src, ".fetch.json")
    if src_fetch0.is_file():  # self-describing: the fetch span and slots travel with the snapshot
        dest_fetch = side(dest, ".fetch.json")
        if dest_fetch.exists():
            raise Refused(f"{dest_fetch} exists; refusing to overwrite")
        _write_new(dest_fetch, src_fetch0.read_text(encoding="utf-8"), readonly=True)
    vmap = pv.load_map(dest)  # counts come from the copy, not the source
    rec = {"utc": utc, "file": dest.name, "sha256": sha256_file(dest), "detail_sha256": sha256_file(dest_detail), "n": len(vmap), "n_null": sum(v is None for v in vmap.values())}
    src_fetch = side(src, ".fetch.json")
    if src_fetch.is_file():  # the fetch span and context slots: what lets `merge` place LP events against this snapshot
        try:
            fd = json.loads(src_fetch.read_text(encoding="utf-8"))
        except ValueError:
            fd = {}
        for k in ("fetch_started_utc", "last_fetch_utc", "fetch_ended_utc", "fetch_slot_min", "fetch_slot_max"):
            if isinstance(fd, dict) and fd.get(k) is not None:
                rec[k] = fd[k]
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
def _unix(ts: Any) -> int | None:
    try:
        return int(datetime.strptime(ts, TS_FMT).replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


class Span(tuple):
    """(start, end) unix seconds. tail_from is set when `end` is an assumption (OLD_FETCH_MAX_MIN after last_fetch_utc)."""

    tail_from: int | None = None
    slot_min: int | None = None
    slot_max: int | None = None


def span_of(doc: dict[str, Any] | None) -> Span | None:
    """Span of a fetch record: fetch_started_utc .. fetch_ended_utc, or, when the record predates fetch_ended_utc,
    last_fetch_utc (a START time) + OLD_FETCH_MAX_MIN minutes, an assumption recorded in the merge meta (tail_from marks
    where the assumed tail begins). None if unusable."""
    if not isinstance(doc, dict):
        return None
    t0 = _unix(doc.get("fetch_started_utc"))
    t1 = _unix(doc.get("fetch_ended_utc"))
    tail = None
    if t1 is None:
        last = _unix(doc.get("last_fetch_utc"))
        if last is not None:
            t1, tail = last + OLD_FETCH_MAX_MIN * 60, last
    if t0 is None or t1 is None or t1 < t0:
        return None
    sp = Span((t0, t1))
    sp.tail_from = tail
    lo, hi = doc.get("fetch_slot_min"), doc.get("fetch_slot_max")
    if isinstance(lo, int) and isinstance(hi, int) and not isinstance(lo, bool) and not isinstance(hi, bool) and lo <= hi:
        sp.slot_min, sp.slot_max = lo, hi
    return sp


def ledger_row(snap: Path) -> dict[str, Any] | None:
    ledger = snap.parent / LEDGER_NAME
    if not ledger.is_file():
        return None
    want = sha256_file(snap)
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("file") == snap.name and r.get("sha256") == want:
            return r
    return None


def snapshot_span(snap: Path, fetch_file: Path | None = None) -> Span | None:
    """A snapshot's fetch span. `fetch_file` (--snapshot-fetch) is accepted only if its detail_sha256 equals the ledger
    row's detail_sha256 for this snapshot (else Refused). Otherwise the snapshot's own .fetch.json sidecar (same check
    against the snapshot's detail sha), else the ledger line's fetch fields."""
    row = ledger_row(snap)
    if fetch_file is not None:
        if row is None:
            raise Refused(f"{snap} is not in its ledger: cannot bind {fetch_file}")
        doc = load_json_dict(fetch_file, "snapshot fetch file")
        if doc.get("detail_sha256") != row.get("detail_sha256"):
            raise Refused(f"{fetch_file}: detail_sha256 {doc.get('detail_sha256')} != the ledger's {row.get('detail_sha256')} for {snap.name}: not this snapshot's fetch")
        return span_of(doc)
    fj = side(snap, ".fetch.json")
    if fj.is_file():
        doc = load_json_dict(fj, "snapshot fetch sidecar")
        if doc.get("detail_sha256") != sha256_file(side(snap, ".detail.json")):
            raise Refused(f"{fj}: detail_sha256 is missing or does not match the snapshot's detail file")
        return span_of(doc)
    return span_of(row) if row else None


class LpHistory:
    """An lphist file with its meta (coverage window) after the meta sha256 check."""

    def __init__(self, path: Path):
        self.path = path
        mp = path.with_name(path.name + ".meta.json")
        if not mp.is_file():
            raise Refused(f"{mp} missing: an lphist file needs its meta")
        self.meta = load_json_dict(mp, "lphist meta")
        self.sha256 = sha256_file(path)
        self.meta_sha256 = sha256_file(mp)
        if self.meta.get("sha256") != self.sha256:
            raise Refused(f"{path}: sha256 {self.sha256} != the meta's {self.meta.get('sha256')}")
        self.data = load_json_dict(path, "lphist file")
        self.t_from, self.t_to = self.meta.get("t_from_unix"), self.meta.get("t_to_unix")
        self.require_slot: int | None = None  # set by merge: the final fetch's fetch_slot_max
        self.require_time: int | None = None  # ... or its end time when it has no slots

    def entry(self, pool: str, t0: int, t1: int) -> tuple[dict[str, Any] | None, str]:
        """(entry, "") when this file has the pool resolved over [t0, t1]; else (None, reason)."""
        e = self.data.get(pool)
        if not isinstance(e, dict):
            return None, "not_in_lphist"
        if not e.get("resolved"):
            return None, f"lphist_unresolved:{e.get('reason')}"
        if not (isinstance(self.t_from, int) and isinstance(self.t_to, int) and self.t_from <= t0 and self.t_to >= t1):
            return None, "lphist_window_does_not_cover_fetches"
        if self.require_slot is not None:  # the history must be complete up to the final fetch
            ss = e.get("supply_slot")
            if not isinstance(ss, int):
                return None, "lphist_supply_slot_missing"
            if ss < self.require_slot:
                return None, "lphist_supply_read_before_final_fetch"
        elif self.require_time is not None:
            rs = self.meta.get("run_start_unix")
            if not isinstance(rs, int) or rs < self.require_time:
                return None, "lphist_supply_read_before_final_fetch"
        return e, ""


def _rel(e: dict[str, Any], sp: Any) -> str:
    """'before' / 'in' / 'after' / 'unknown': where the event falls against a fetch. By slot when the event has a slot and
    the fetch has fetch_slot_min/max; else by block time (1 s of slack); else unknown."""
    slot, lo, hi = e.get("slot"), getattr(sp, "slot_min", None), getattr(sp, "slot_max", None)
    if isinstance(slot, int) and lo is not None and hi is not None:
        return "before" if slot < lo else ("after" if slot > hi else "in")
    t = e.get("block_time")
    if t is None:
        return "unknown"
    return "before" if t < sp[0] - 1 else ("after" if t > sp[1] + 1 else "in")


def classify_events(events: Sequence[dict[str, Any]], a: Any, b: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(between, ambiguous) for two fetches a (earlier) and b (later). Placement is by slot where the event has a slot and
    the fetch recorded fetch_slot_min/max (event slot < min: before the fetch; > max: after; else inside), and by block time
    (1 s of slack) for old fetches without slots. Events before a or after b are outside; events inside either fetch may
    fall on either side of that fetch's read (unknown placement counts as ambiguous); the rest are definite."""
    between: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for e in events:
        ra, rb = _rel(e, a), _rel(e, b)
        if ra == "before" or rb == "after":
            continue
        if "unknown" in (ra, rb) or ra == "in" or rb == "in":
            ambiguous.append(e)
        else:
            between.append(e)
    return between, ambiguous


def _b(detail: dict[str, Any], p: str) -> int | None:
    d = detail.get(p)
    b = d.get("v_base") if isinstance(d, dict) else None
    return b if isinstance(b, int) and not isinstance(b, bool) else None


def _v0(detail: dict[str, Any], p: str, v: int | None) -> int | None:
    """V0 of a pool from its detail entry and stored V. v_base when present. An account with no pending counters
    (entry present, pending None and v_base None: shorter than 287 bytes) has V0 = its stored V (DEC-016 Amendment 5
    clarification); never applied when pending is an int."""
    b = _b(detail, p)
    if b is not None:
        return b
    d = detail.get(p)
    if isinstance(d, dict) and d.get("pending") is None and d.get("v_base") is None and isinstance(v, int) and not isinstance(v, bool):
        return v
    return None


def classify_pool(p: str, reads: Sequence[tuple[int | None, tuple[int, int] | None]], lphists: Sequence[LpHistory], tally: dict[str, int] | None = None) -> tuple[str, str]:
    """('ok'|'explained'|'unexplained'|'unresolved', reason) for one pool's V0 reads, oldest first. Consecutive reads
    that differ must be explained by the pool's LP events (DEC-016 Amendment 5 section 7(b))."""
    status = "ok"
    for (v_a, sp_a), (v_b, sp_b) in zip(reads, reads[1:]):
        if v_a is None or v_b is None:
            return "unresolved", "no_v_base"
        if v_a == v_b:
            continue
        if sp_a is None or sp_b is None:
            return "unresolved", "no_fetch_span"
        t0, t1 = sp_a[0], sp_b[1]
        entry, why = None, "not_in_lphist"
        for h in lphists:
            entry, w = h.entry(p, t0, t1)
            if entry is not None:
                break
            if w != "not_in_lphist":
                why = w
        if entry is None:
            return "unresolved", why
        between, ambiguous = classify_events(entry["events"], sp_a, sp_b)
        if tally is not None:
            for sp in (sp_a, sp_b):
                tf = getattr(sp, "tail_from", None)
                if tf is not None:  # events inside an assumed fetch tail (tried both ways like any in-span event)
                    tally["events_in_assumed_tail"] = tally.get("events_in_assumed_tail", 0) + sum(1 for e in ambiguous if e.get("block_time") is not None and tf - 1 <= e["block_time"] <= sp[1] + 1)
        ok = lph.consistent(v_a, v_b, between, ambiguous)
        if ok is None:
            return "unresolved", "too_many_ambiguous_events"
        if not ok:
            return "unexplained", "lp_rule_fails"
        status = "explained"
    return status, ""


def merge_maps(final: dict[str, int | None], snaps: Sequence[dict[str, int | None]], pool_set: set[str], reasons: dict[str, str] | None = None, final_detail: dict[str, Any] | None = None, snap_details: Sequence[dict[str, Any]] | None = None, spans: Sequence[tuple[int, int] | None] | None = None, lphists: Sequence[LpHistory] = (), strict_reasons: bool = True) -> tuple[dict[str, int | None], list[str], list[str], dict[str, Any]]:
    """(merged, filled, ignored_outside_set, classes).

    V0 (= v_base) moves only at LP events. For each pool of pool_set with two or more non-null reads (snapshots oldest
    first, then the final map; `spans` gives each fetch's (start, end) unix seconds, snapshots then final), consecutive
    differing V0 values are checked against the LP history (classify_pool). classes = {"explained": [ids],
    "unexplained": [ids], "unresolved": {id: reason}}. Only pools in pool_set are filled; only closed-null or absent pools
    are filled; a null with reason "unreadable" or with no reason refuses; a pool that is unexplained or unresolved is
    never filled. A fill copies the v of the LAST snapshot given."""
    reasons = reasons or {}
    final_detail = final_detail or {}
    snap_details = snap_details if snap_details is not None else [{} for _ in snaps]
    spans = list(spans) if spans is not None else [None] * (len(snaps) + 1)
    snap_v: dict[str, int] = {}
    for sm in snaps:
        for p, v in sm.items():
            if v is not None:
                snap_v[p] = v
    classes: dict[str, Any] = {"explained": [], "unexplained": [], "unresolved": {}}
    tally: dict[str, int] = {}
    for p in sorted(pool_set):
        reads: list[tuple[int | None, tuple[int, int] | None]] = []
        for i, (sm, sd) in enumerate(zip(snaps, snap_details)):
            if sm.get(p) is not None:
                reads.append((_v0(sd, p, sm[p]), spans[i]))
        if final.get(p) is not None:
            reads.append((_v0(final_detail, p, final[p]), spans[len(snaps)]))
        if len(reads) < 2:
            continue
        status, why = classify_pool(p, reads, lphists, tally)
        if status == "explained":
            classes["explained"].append(p)
        elif status == "unexplained":
            classes["unexplained"].append(p)
        elif status == "unresolved":
            classes["unresolved"][p] = why
    classes["events_in_assumed_tail"] = tally.get("events_in_assumed_tail", 0)
    held = set(classes["unexplained"]) | set(classes["unresolved"])
    out = dict(final)
    want = sorted(p for p, v in snap_v.items() if out.get(p) is None)
    ignored = [p for p in want if p not in pool_set]
    filled = [p for p in want if p in pool_set and p not in held]
    no_reason = sorted(p for p in pool_set if p in final and final[p] is None and reasons.get(p) not in ("closed", "unreadable"))
    if not strict_reasons:  # dry run only: a stand-in final has no reasons file; such pools are not filled, not refused
        filled = [p for p in filled if p not in set(no_reason)]
        classes["n_null_no_reason_dry_run"] = len(no_reason)
        no_reason = []
    if no_reason:
        raise Refused(f"{len(no_reason)} pool(s) in the set are null in the final map with no recorded reason: never filled; refetch with this tool")
    bad = [p for p in filled if p in final and reasons.get(p) == "unreadable"]
    if bad:
        raise Refused(f"{len(bad)} pool(s) are null in the final map as unreadable but non-null in a snapshot: not filled; resolve first")
    for p in filled:
        out[p] = snap_v[p]
    for p in held:  # unexplained / unresolved: null in OUT so any consumer prices them as null-V
        if p in pool_set:
            out[p] = None
    return out, filled, ignored, classes



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
    dry = bool(getattr(a, "dry_run", False))
    if dry != out.name.endswith(DRYRUN_SUFFIX):
        raise Refused(f"--dry-run output must end {DRYRUN_SUFFIX}, and a real merge output must not")
    final_path = Path(a.final)
    snap_paths = [Path(s) for s in a.snapshot]
    if dry:  # rehearsal: the latest ledgered snapshot stands in for the final; no post-cutoff / fetch --new checks
        check_in_ledger(final_path)
        final_fetch = {"dry_run": True, "stand_in_snapshot": final_path.name}
    else:
        final_fetch = final_fetch_block(final_path, Path(a.pools))
    snap_paths = sorted(snap_paths, key=lambda x: x.name)  # oldest first (UTC in the name); a fill copies the latest
    for sp in snap_paths:
        check_in_ledger(sp)
    pool_set = set(json.loads(Path(a.pools).read_text(encoding="utf-8")))
    if not dry and not side(final_path, ".reasons.json").is_file():
        raise Refused(f"{side(final_path, '.reasons.json')} missing: run the final fetch with this tool so null reasons are recorded")
    reasons = load_reasons(final_path)
    final = pv.load_map(final_path)
    final_detail = load_detail(final_path)
    if not dry and final_fetch["detail_sha256"] != sha256_file(side(final_path, ".detail.json")):
        raise Refused(f"{side(final_path, '.detail.json')} does not match detail_sha256 in the fetch file")
    snap_details = [load_detail(sp) for sp in snap_paths]
    lphists = [LpHistory(Path(x)) for x in (a.lphist or [])]
    for i, h1 in enumerate(lphists):  # first-run-only: no pool may come from two overlapping histories
        for h2 in lphists[i + 1 :]:
            if all(isinstance(x, int) for x in (h1.t_from, h1.t_to, h2.t_from, h2.t_to)) and h1.t_from <= h2.t_to and h2.t_from <= h1.t_to and set(h1.data) & set(h2.data):
                raise Refused(f"{h1.path.name} and {h2.path.name} cover the same pool over overlapping windows; give one history per pool")
    fetch_over: dict[str, Path] = {}
    for item in a.snapshot_fetch or []:
        name, sep, fpath = item.partition("=")
        if not sep or not fpath:
            raise Refused(f"--snapshot-fetch {item!r}: expected SNAPFILE=FETCHJSON")
        fetch_over[Path(name).name] = Path(fpath)
    if set(fetch_over) - {sp.name for sp in snap_paths} - ({final_path.name} if dry else set()):
        raise Refused("--snapshot-fetch names a file that is not one of the --snapshot arguments")
    if dry:  # the stand-in's span is bound the same way as any snapshot's: ledger detail_sha256
        final_span = snapshot_span(final_path, fetch_over.get(final_path.name))
    else:
        final_span = span_of(load_json_dict(side(final_path, ".fetch.json"), "fetch file"))
    for h in lphists:
        h.require_slot = final_span.slot_max if final_span is not None else None
        h.require_time = final_span[1] if final_span is not None else None
    spans = [snapshot_span(sp, fetch_over.get(sp.name)) for sp in snap_paths] + [final_span]
    side_paths = {"unexplained": side(out, ".unexplained.json"), "unresolved": side(out, ".unresolved.json")}
    merged, filled, ignored, classes = merge_maps(final, [pv.load_map(s) for s in snap_paths], pool_set, reasons, final_detail, snap_details, spans, lphists, strict_reasons=not dry)
    for k, sp in side_paths.items():  # ids stay in files; diagnostics, so a refused run may rewrite them
        if sp.exists():
            os.chmod(sp, 0o644)
            sp.unlink()
        _write_new(sp, json.dumps(classes[k], sort_keys=True) + "\n", readonly=True)
    n_moved_bad = len(classes["unexplained"]) + len(classes["unresolved"])
    if n_moved_bad > MOVED_CEILING * len(pool_set):
        raise Refused(f"{len(classes['unexplained'])} unexplained + {len(classes['unresolved'])} unresolved V0 moves exceed {MOVED_CEILING:.1%} of {len(pool_set)} pools (ids in {side_paths['unexplained'].name}, {side_paths['unresolved'].name}); (B) NOT_DECIDABLE until resolved")
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
        "dry_run": dry,
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
        "unreadable_pools": unreadable,
        # DEC-016 Amendment 5 section 7(b): vbook treats the unexplained and unresolved pools as null-V; ids only in the files.
        "lp_moves": {
            "n_explained": len(classes["explained"]),
            "n_unexplained": len(classes["unexplained"]),
            "n_unresolved": len(classes["unresolved"]),
            "n_nulled_in_out": len(classes["unexplained"]) + len(classes["unresolved"]),
            "ceiling": MOVED_CEILING,
            "unexplained_file": side_paths["unexplained"].name,
            "unexplained_sha256": sha256_file(side_paths["unexplained"]),
            "unresolved_file": side_paths["unresolved"].name,
            "unresolved_sha256": sha256_file(side_paths["unresolved"]),
            "lphist": [{"file": h.path.name, "sha256": h.sha256, "meta_sha256": h.meta_sha256, "t_from_unix": h.t_from, "t_to_unix": h.t_to, "n_pools": h.meta.get("n_pools"), "n_unresolved": h.meta.get("n_unresolved")} for h in lphists],
            "fetch_spans_unix": [list(s) if s else None for s in spans],
            "snapshot_fetch_sha256": {n: sha256_file(f) for n, f in sorted(fetch_over.items())},
            "snapshot_fetch_sidecar_sha256": {sp.name: sha256_file(side(sp, ".fetch.json")) for sp in snap_paths if side(sp, ".fetch.json").is_file()},
            "span_assumption": {"OLD_FETCH_MAX_MIN": OLD_FETCH_MAX_MIN, "note": "a fetch record without fetch_ended_utc is assumed to have ended by last_fetch_utc + OLD_FETCH_MAX_MIN", "n_spans_with_assumed_end": sum(1 for s in spans if getattr(s, "tail_from", None) is not None), "n_events_in_assumed_tail": classes["events_in_assumed_tail"]},
        },
        "sha256": {"final": sha256_file(final_path), "pools": sha256_file(Path(a.pools)), "reasons": sha256_file(side(final_path, ".reasons.json")) if side(final_path, ".reasons.json").is_file() else None, "final_detail": sha256_file(side(final_path, ".detail.json")), "snapshot_details": [sha256_file(side(sp, ".detail.json")) for sp in snap_paths], "fetch": sha256_file(side(final_path, ".fetch.json")) if side(final_path, ".fetch.json").is_file() else None, "snapshots": [sha256_file(s) for s in snap_paths], "out": sha256_file(out)},
    }
    _write_new(meta_path, json.dumps(meta, indent=1, sort_keys=True) + "\n", readonly=True)
    print(f"merge: n={meta['n']} filled={len(filled)} ignored={len(ignored)} n_null_after={meta['n_null_after']} n_absent_after={meta['n_absent_after']} n_unreadable={len(unreadable)} -> {out}")
    return 0


def load_pool_ids(path: Path) -> list[str]:
    """A pools file: a JSON list of ids, or a `diffs` output ({"diffs": [{"pool": id, ...}]})."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and isinstance(doc.get("diffs"), list):
        doc = [d.get("pool") for d in doc["diffs"] if isinstance(d, dict)]
    if not isinstance(doc, list) or not all(isinstance(x, str) for x in doc):
        raise Refused(f"{path} is not a list of pool ids")
    return doc


def cmd_diffs(a: argparse.Namespace) -> int:
    """Pools of --pools whose V0 (v_base) differs between two detail files; ids and both values go to --out only."""
    out = Path(a.out)
    if out.exists():
        raise Refused(f"{out} exists; refusing to overwrite")
    da = load_json_dict(Path(a.a), "detail file a")
    db = load_json_dict(Path(a.b), "detail file b")
    diffs = []
    for p in sorted(set(load_pool_ids(Path(a.pools)))):
        x, y = _b(da, p), _b(db, p)
        if x is not None and y is not None and x != y:
            diffs.append({"pool": p, "a": x, "b": y})
    _write_new(out, json.dumps({"a_sha256": sha256_file(Path(a.a)), "b_sha256": sha256_file(Path(a.b)), "diffs": diffs}, indent=1) + "\n")
    print(f"diffs: n={len(diffs)} -> {out}")
    return 0


def cmd_lphist(a: argparse.Namespace, rpc: Callable[[str, list], Any] | None = None) -> int:
    """LP Deposit/Withdraw events of --pools over [--from, --to]. `rpc` (tests) replaces the real Helius client;
    the key is read inside Python from the env file and the URL is never printed."""
    if a.rps > pv.MAX_RPS:
        raise Refused(f"rps {a.rps} > {pv.MAX_RPS}: walkers share Helius")
    out = Path(a.out)
    meta_path = side(out, ".meta.json")
    for p in (out, meta_path):
        if p.exists():
            raise Refused(f"{p} exists; refusing to overwrite")
    t0, t1 = _unix(a.t_from), _unix(a.t_to)
    if t0 is None or t1 is None or t1 < t0:
        raise Refused("--from/--to must be ISO UTC like 2026-10-05T00:00:00Z, with from <= to")
    run_start = datetime.now(timezone.utc)
    if t1 > int(run_start.timestamp()):
        raise Refused("--to is after the run start: the history must end at or before the moment it is read")
    pools = sorted(set(load_pool_ids(Path(a.pools))))
    if rpc is None:
        from tools.pumpswap_simulate import Rpc

        rpc = Rpc(pv._rpc_url())
    kw = {"sleep": time.sleep} if a.rps else {}
    attempts: list[dict[str, Any]] = []
    hist, calls = lph.fetch_lp_history(rpc, pools, t0, t1, rps=a.rps, attempts=attempts, **kw)
    _write_new(out, json.dumps(hist, sort_keys=True) + "\n", readonly=True)
    n_unres = sum(1 for e in hist.values() if not e["resolved"])
    meta = {"sha256": sha256_file(out), "n_pools": len(hist), "n_unresolved": n_unres, "n_events": sum(len(e["events"]) for e in hist.values()), "calls": calls, "utc": datetime.now(timezone.utc).strftime(TS_FMT), "run_start_utc": run_start.strftime(TS_FMT), "run_start_unix": int(run_start.timestamp()), "supply_slot_min": min([e["supply_slot"] for e in hist.values() if isinstance(e.get("supply_slot"), int)], default=None), "t_from_unix": t0, "t_to_unix": t1, "from": a.t_from, "to": a.t_to, "pools_sha256": sha256_file(Path(a.pools)), "attempts": attempts, "reasons": {r: sum(1 for e in hist.values() if e["reason"] == r) for r in sorted({e["reason"] for e in hist.values() if e["reason"]})}}
    _write_new(meta_path, json.dumps(meta, indent=1, sort_keys=True) + "\n", readonly=True)
    print(f"lphist: n={len(hist)} n_unresolved={n_unres} calls={calls} -> {out}")
    return 0


def load_merge_meta(path: Path) -> dict[str, Any]:
    """The merge record a consumer (vbook) binds to. A dry-run meta, or one for an output named *.dryrun.json, is refused:
    a rehearsal can never stand in for the FINAL map."""
    doc = load_json_dict(path, "merge meta")
    if doc.get("dry_run") is True or path.name.endswith(DRYRUN_SUFFIX + ".merge.json"):
        raise Refused(f"{path}: a dry-run merge record cannot be used for the FINAL")
    return doc


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
    p = sub.add_parser("diffs")
    p.add_argument("--a", required=True, help="older MAP.detail.json")
    p.add_argument("--b", required=True, help="newer MAP.detail.json")
    p.add_argument("--pools", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_diffs)
    p = sub.add_parser("lphist")
    p.add_argument("--pools", required=True, help="a pools file, or a `diffs` output")
    p.add_argument("--from", dest="t_from", required=True)
    p.add_argument("--to", dest="t_to", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--rps", type=float, default=pv.MAX_RPS)
    p.set_defaults(fn=cmd_lphist)
    p = sub.add_parser("snapshot")
    p.add_argument("--vmap", required=True)
    p.add_argument("--out", required=True, help="directory; file is vmap-snapshot-<UTC>.json, ledger is snapshots.jsonl")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("merge")
    p.add_argument("--final", required=True)
    p.add_argument("--pools", required=True, help="the section 1 pool set (pools.json)")
    p.add_argument("--snapshot", action="append", required=True)
    p.add_argument("--snapshot-fetch", action="append", default=[], help="SNAPFILE=FETCHJSON: a fetch record for a snapshot; accepted only if its detail_sha256 equals the ledger row's (repeatable)")
    p.add_argument("--dry-run", action="store_true", help=f"rehearsal: --final is the latest ledgered snapshot; OUT must end {DRYRUN_SUFFIX}; the merge meta says dry_run and the FINAL path refuses it")
    p.add_argument("--lphist", action="append", default=[], help="an lphist output (its .meta.json sha256 is checked); repeatable")
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
