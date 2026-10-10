#!/usr/bin/env python3
"""Memory and equivalence probe for the C1-NF shadow's wallet-ledger snapshot (tools/c1nf_shadow.py `_AsofSnapshot` vs `_PreadSnapshot`).

Reads ONE as-of snapshot directory (`<root>/asof/asof-<day>`) of tools/c1nf_wallet_ledger.py. Never opens a tape, a pick or an outcome file. Use an
exploration-built root (for example /data/mal/hunt-1008/c1nf-ledger/item7-0920), not the live one.

  mem    --root R --day D --impl old|new [--hash fake|duck] [--seed 1]
         One process, one implementation. Prints one JSON line per stage with VmRSS / RssAnon / RssFile (kB, from /proc/self/status): after
         import, after the duckdb hash, after `AsofLedger.open_for_day`, after the snapshot is built, and after growing batches of distinct known
         wallets, absent wallets, and real duckdb-hashed strings. The wallet sample is drawn with pread, so the probe itself maps nothing.
  equiv  --root R --day D
         Builds the old and the new snapshot on the same ledger in one process and compares get() for EVERY wallet in th order (types and float
         bits), plus absent wallets (random, edges, neighbours of known keys). Prints a counts-only md5 over (wallet, 7 float64 values) in sorted
         order for old, for new, and for the ledger's own passa_matrix. Exit 0 only when all three are equal and no wallet differs.

The wallet "name" handed to get() is the decimal string of its th, and the hash function is int(name): that is the seam `_AsofSnapshot` and
`_PreadSnapshot` share (`hash_fn(trader) -> int`), so the compare covers the ledger lookup and the value arithmetic, not the duckdb hash (which
`--hash duck` in `mem` exercises separately, after #502's check_hash_pin).
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import random
import struct
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

STATUS_KEYS = ("VmRSS", "RssAnon", "RssFile", "RssShmem", "VmHWM")
PASSA_COLS = ("n", "nbond", "cash", "nwin", "nrt", "ndays", "buy")
U64_MAX = (1 << 64) - 1


def proc_status() -> dict[str, int]:
    out: dict[str, int] = {}
    with open("/proc/self/status") as fh:
        for line in fh:
            k, _, v = line.partition(":")
            if k in STATUS_KEYS:
                out[k] = int(v.split()[0])
    return out


def asof_path(root: Path, day: str) -> Path:
    return root / "asof" / f"asof-{day}"


def npy_layout(path: Path) -> tuple[int, np.dtype, int]:
    """(data offset, dtype, rows) of a 1-D .npy, from its header only."""
    with open(path, "rb") as fh:
        ver = np.lib.format.read_magic(fh)
        hdr = np.lib.format.read_array_header_1_0 if ver == (1, 0) else np.lib.format.read_array_header_2_0
        shape, fortran, dtype = hdr(fh)
        return fh.tell(), np.dtype(dtype), int(shape[0])


def pread_u64(path: Path, rows: np.ndarray) -> list[int]:
    """th[rows] by pread (no mapping, so the probe's own RSS stays clean)."""
    off, dt, _ = npy_layout(path)
    fd = os.open(path, os.O_RDONLY)
    try:
        return [int.from_bytes(os.pread(fd, 8, off + int(i) * 8), "little") for i in rows]
    finally:
        os.close(fd)


def deep_cache_bytes(cache: dict) -> int:
    n = sys.getsizeof(cache)
    for k, v in cache.items():
        n += sys.getsizeof(k)
        n += sys.getsizeof(v)
        if isinstance(v, tuple):
            n += sum(sys.getsizeof(x) for x in v)
    return n


def make_snapshot(impl: str, led: Any, hash_fn: Callable[[str], int]):
    import tools.c1nf_shadow as cs

    if impl == "old":
        return cs._AsofSnapshot(led, hash_fn)
    if impl == "new":
        return cs._PreadSnapshot.from_ledger(led, hash_fn)
    raise SystemExit(f"--impl {impl!r}: old or new")


# ---- mem -----------------------------------------------------------------------------------------------------------------------------------
def cmd_mem(a: argparse.Namespace) -> int:
    stages: list[dict] = []

    def mark(name: str, **extra: Any) -> None:
        rec = {"stage": name, **proc_status(), **extra}
        stages.append(rec)
        print(json.dumps(rec), flush=True)

    root, day = Path(a.root), a.day
    d = asof_path(root, day)
    mark("start", impl=a.impl, kernel=platform.release(), numpy=np.__version__, python=platform.python_version())
    import tools.c1nf_shadow as cs

    mark("import_shadow")
    man = json.loads((d / "MANIFEST.json").read_text())
    files = {p.name: p.stat().st_size for p in sorted(d.glob("*.npy"))}
    _, _, n = npy_layout(d / "th.npy")
    # Sample first (pread), so that nothing below is charged for the sampling.
    rng = np.random.default_rng(a.seed)
    k_max = int(a.max_known)
    rows = rng.integers(0, n, size=k_max)
    known_th = pread_u64(d / "th.npy", rows)
    absent_th = [int(x) for x in rng.integers(0, 2**63, size=int(a.max_absent), dtype=np.uint64) * np.uint64(2) + np.uint64(1)]
    rnd = random.Random(a.seed)
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    strings = ["".join(rnd.choice(alphabet) for _ in range(44)) for _ in range(int(a.duck_strings))]
    names_known = [f"k{i}" for i in range(k_max)]
    table = dict(zip(names_known, known_th))
    table.update({f"a{i}": h for i, h in enumerate(absent_th)})
    fake = table.__getitem__
    mark("sampled", wallets=n, known_sample=k_max, absent_sample=len(absent_th), duck_strings=len(strings))

    duck = None
    if a.hash == "duck":
        duck = cs._duck_hash()
        duck("abc")
        mark("duckdb_hash_built")
    from tools.c1nf_wallet_ledger import AsofLedger

    mark("import_ledger_module")
    led = AsofLedger.open_for_day(root, day)
    mark("after_open_for_day", wallets=len(led))
    snap = make_snapshot(a.impl, led, fake)
    if a.impl == "new":
        del led                                  # the production path (AsofDirLedger.snapshot_for_day) keeps no reference to the ledger either
        gc.collect()
    mark("after_snapshot_built", snapshot_cls=type(snap).__name__)

    first = snap.get(names_known[0])
    mark("after_first_known_get", value=list(first) if first else None)

    done, t_all = 1, 0.0
    steps = [int(x) for x in a.steps.split(",")]
    for target in steps:
        target = min(target, k_max)
        t0 = time.perf_counter()
        for i in range(done, target):
            snap.get(names_known[i])
        dt_s = time.perf_counter() - t0
        t_all += dt_s
        cache = getattr(snap, "_cache", {})
        mark(f"known_gets_{target}", distinct=target, step_us_per_get=round(dt_s / max(target - done, 1) * 1e6, 2),
             cache_entries=len(cache))
        done = max(done, target)

    t0 = time.perf_counter()
    na = len(absent_th)
    for i in range(na):
        snap.get(f"a{i}")
    mark(f"absent_gets_{na}", us_per_get=round((time.perf_counter() - t0) / max(na, 1) * 1e6, 2),
         cache_entries=len(getattr(snap, "_cache", {})))

    # Warm repeat: the same known wallets again (cache hits, or the same preads).
    t0 = time.perf_counter()
    rep = min(done, 20000)
    for i in range(rep):
        snap.get(names_known[i])
    mark("repeat_known_gets", n=rep, us_per_get=round((time.perf_counter() - t0) / max(rep, 1) * 1e6, 2))

    if duck is not None and strings:
        snap_d = make_snapshot(a.impl, AsofLedger.open_for_day(root, day), duck)
        t0 = time.perf_counter()
        hits = sum(snap_d.get(s) is not None for s in strings)
        mark("duckdb_hashed_strings", n=len(strings), hits=hits,
             us_per_get=round((time.perf_counter() - t0) / len(strings) * 1e6, 2))
        del snap_d

    cache = getattr(snap, "_cache", {})
    sizes = {
        "wallets": n,
        "files_bytes": files,
        "files_total_bytes": sum(v for k, v in files.items() if k != "MANIFEST.json"),
        "bytes_per_wallet_on_disk": round(sum(files.values()) / n, 3),
        "th_bytes_per_wallet": 8,
        "cache_entries": len(cache),
        "cache_deep_bytes": deep_cache_bytes(cache),
        "cache_bytes_per_entry": round(deep_cache_bytes(cache) / max(len(cache), 1), 1),
        "manifest_asof_day": man.get("asof_day"),
    }
    del snap
    gc.collect()
    mark("after_del_snapshot")
    base = {s["stage"]: s for s in stages}
    summary = {
        "impl": a.impl,
        "sizes": sizes,
        "rss_kB": {k: base[k]["VmRSS"] for k in base},
        "rss_file_kB": {k: base[k]["RssFile"] for k in base},
        "rss_anon_kB": {k: base[k]["RssAnon"] for k in base},
        "peak_VmHWM_kB": stages[-1]["VmHWM"],
        "extrapolation_16_56M": {
            "ledger_files_bytes_if_fully_resident": int(16_560_000 * sizes["files_total_bytes"] / n),
            "cache_bytes_at_500k_entries": int(500_000 * sizes["cache_bytes_per_entry"]),
        },
    }
    print("SUMMARY " + json.dumps(summary), flush=True)
    return 0


# ---- equiv ---------------------------------------------------------------------------------------------------------------------------------
def pack7(v: Any) -> bytes:
    return struct.pack("<7d", *v)


def cmd_equiv(a: argparse.Namespace) -> int:
    import tools.c1nf_shadow as cs
    from tools.c1nf_wallet_ledger import AsofLedger

    root, day = Path(a.root), a.day
    led = AsofLedger.open_for_day(root, day)
    th = led.th
    n = len(th)
    print(json.dumps({"stage": "opened", "wallets": n, **proc_status()}), flush=True)
    old = cs._AsofSnapshot(led, int)
    new = cs._PreadSnapshot.from_ledger(led, int)
    assert type(new).__name__ == "_PreadSnapshot"
    # Disable the old cache bound effects: the cache returns the stored tuple, which is the same value, but keep it small for memory.
    old_h, new_h, ref_h = hashlib.md5(), hashlib.md5(), hashlib.md5()
    bad: list[dict] = []
    known_n = 0
    chunk = 1 << 16
    t0 = time.time()

    def same(x: Any, y: Any) -> bool:
        if x is None or y is None:
            return x is None and y is None
        return (type(x) is type(y) and len(x) == len(y) and all(type(p) is float and type(q) is float for p, q in zip(x, y))
                and pack7(x) == pack7(y))

    for lo in range(0, n, chunk):
        hi = min(lo + chunk, n)
        ths = np.asarray(th[lo:hi])
        known, m = led.passa_matrix(ths)               # the ledger's own matrix, vectorised
        assert bool(known.all())
        for j in range(hi - lo):
            w = int(ths[j])
            name = str(w)
            vo, vn = old.get(name), new.get(name)
            if not same(vo, vn) or vo is None or pack7(vo) != m[j].tobytes():
                if len(bad) < 20:
                    bad.append({"wallet": w, "old": vo, "new": vn, "ref": m[j].tolist()})
            wb = struct.pack("<Q", w)
            old_h.update(wb + (pack7(vo) if vo is not None else b"NONE"))
            new_h.update(wb + (pack7(vn) if vn is not None else b"NONE"))
            ref_h.update(wb + m[j].tobytes())
            known_n += 1
        if (lo // chunk) % 20 == 0:
            print(json.dumps({"stage": "progress", "done": hi, "of": n, "bad": len(bad), "secs": round(time.time() - t0, 1)}), flush=True)
        old._cache.clear()
        new._cache.clear() if hasattr(new, "_cache") else None
    # Absent wallets.
    rng = np.random.default_rng(a.seed)
    cand = [int(x) for x in rng.integers(0, 2**63, size=a.absent, dtype=np.uint64) * np.uint64(2) + np.uint64(rng.integers(0, 2))]
    edges = [0, 1, U64_MAX, U64_MAX - 1, 2**63, 2**63 - 1]
    first, last = int(th[0]), int(th[n - 1])
    edges += [x for x in (first - 1, first + 1, last - 1, last + 1) if 0 <= x <= U64_MAX]
    sample = np.asarray(th[rng.integers(0, n, size=min(a.absent, 200_000))])
    near = [int(x) + d for x in sample for d in (-1, 1) if 0 <= int(x) + d <= U64_MAX]
    probe = cand + edges + near
    known_set_hits = 0
    abs_bad = 0
    abs_old, abs_new = hashlib.md5(), hashlib.md5()
    for w in probe:
        name = str(w)
        vo, vn = old.get(name), new.get(name)
        if not same(vo, vn):
            abs_bad += 1
            if len(bad) < 20:
                bad.append({"wallet": w, "old": vo, "new": vn, "absent_probe": True})
        if vo is not None:
            known_set_hits += 1
        abs_old.update(struct.pack("<Q", w) + (pack7(vo) if vo is not None else b"NONE"))
        abs_new.update(struct.pack("<Q", w) + (pack7(vn) if vn is not None else b"NONE"))
        if len(old._cache) > 100_000:
            old._cache.clear()
    # A hash outside uint64 must fail the same way in both.
    errs = []
    for h in (-1, 1 << 64):
        r = []
        for snap in (old, new):
            try:
                snap._hash = lambda _s, h=h: h
                snap._cache.clear() if hasattr(snap, "_cache") else None
                snap.get(f"bad{h}")
                r.append("no error")
            except Exception as exc:  # noqa: BLE001
                r.append(type(exc).__name__)
        errs.append({"hash": h, "old": r[0], "new": r[1]})
    res = {
        "wallets": n,
        "compared_known": known_n,
        "mismatches": len(bad),
        "mismatch_examples": bad,
        "absent_probes": len(probe),
        "absent_probes_that_hit_a_known_wallet": known_set_hits,
        "absent_mismatches": abs_bad,
        "md5_old": old_h.hexdigest(),
        "md5_new": new_h.hexdigest(),
        "md5_ledger_passa_matrix": ref_h.hexdigest(),
        "md5_absent_probe_old": abs_old.hexdigest(),
        "md5_absent_probe_new": abs_new.hexdigest(),
        "out_of_range_hash_errors": errs,
        "secs": round(time.time() - t0, 1),
        "manifest_content_sha256": led.manifest.get("content_sha256"),
        **proc_status(),
    }
    ok = (not bad and abs_bad == 0 and res["md5_old"] == res["md5_new"] == res["md5_ledger_passa_matrix"]
          and res["md5_absent_probe_old"] == res["md5_absent_probe_new"] and all(e["old"] == e["new"] for e in errs))
    res["equal"] = bool(ok)
    print("RESULT " + json.dumps(res), flush=True)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mem")
    m.add_argument("--root", required=True)
    m.add_argument("--day", required=True)
    m.add_argument("--impl", choices=("old", "new"), required=True)
    m.add_argument("--hash", choices=("fake", "duck"), default="duck")
    m.add_argument("--seed", type=int, default=1)
    m.add_argument("--max-known", type=int, default=200_000)
    m.add_argument("--max-absent", type=int, default=50_000)
    m.add_argument("--duck-strings", type=int, default=20_000)
    m.add_argument("--steps", default="10,100,1000,3000,10000,30000,100000,200000")
    e = sub.add_parser("equiv")
    e.add_argument("--root", required=True)
    e.add_argument("--day", required=True)
    e.add_argument("--seed", type=int, default=1)
    e.add_argument("--absent", type=int, default=2_000_000)
    a = ap.parse_args(argv)
    return cmd_mem(a) if a.cmd == "mem" else cmd_equiv(a)


if __name__ == "__main__":
    raise SystemExit(main())
