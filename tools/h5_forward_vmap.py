#!/usr/bin/env python3
"""EXP-024 Look 1 forward vmap: the {"v": {pool: V|null}} file that `tools/h5_forward_extract.py forward --vmap` reads.

    python -m tools.h5_forward_vmap ev    --vjoin /data/mal/exp024/p5/vjoin --out FILE   # Am.1 source 1 (forward_v_join join)
    python -m tools.h5_forward_vmap gettx --p5 /data/mal/exp024/p5 --out FILE            # only after P5 line A FAILED

POOLS. Every PumpSwap pool (venue "pumpswap", pool a string) printed in forward-1002's 146 Look 1 hours
[2026-10-09T23, 2026-10-16T01) (EXP-024 section 3; the extractor's FWD_FROM/FWD_TO, boostfloor_read.READ_HOURS) is a key, because
the extractor drops a pool that is absent from its vmap. A pool's FIRST print is its earliest row in those hours, in the order the
extractor gives a path: (slot, tx_index, event_index, hour, line), a null tx_index or event_index last. No other hour is read, and
there is no hour flag. This producer takes the earliest print anywhere in the 146 hours. The extractor's s0 is the earliest print at
or after the mint's `complete` slot. The two differ only for a pool that printed before its mint's `complete`.

ev (EXP-024 Am.1 source 1). V = the `virtual_quote_reserves` that `tools/forward_v_join.py join` (default `--emit v`) carried from
forward-1002ev onto that first print, null when the join gave none (a refused or bad hour, or a row on its fallback list: unmatched, a
field that differs, or an ev row with no V). Before any row is read, the join's output is checked: join-report.json covers exactly the
146 hours on this base dir, its decoder pins are all ok, every v-<hour> file is for a usable hour and hashes to the report's sha256,
and no refused hour has one. Sources 2 and 3 do not exist at this stage (P5 plans its fetches from the extract this map feeds), so
nothing else fills a null; the extractor keeps a null-V pool as a candidate and #476 resolves V0 by the Am.1 order. If the P5 dir
next to --vjoin already holds a cross_source.json whose line_a_pass is not true, ev refuses (use gettx). --vjoin must be named
`vjoin` (p5/vjoin), so that this check reads the P5 dir's cross_source.json and not another directory's.

gettx (the line-A fail branch). Am.1: "If line A fails, forward-1002ev is not used for Look 1: `getTransaction` (source 2, then
source 3) is used for every print section 4 fetches (s0, the landing state, the exit state)". So the rebuild opens no ev byte and
takes V, per pool, in this order:
  2. P5's getTransaction record of the first print (`boostfloor_inputs p5` -> p5/gettx_v.jsonl), matched on (slot, signature,
     event_index): status ok, fields_equal true, its content key equal to the tape row's, the decode's pool equal to the pool, and
     an integer `virtual_quote_reserves` (the same acceptance as boostfloor_read.load_gettx, plus the pool);
  3. the pool account V0 (`boostfloor_inputs account` -> p5/account_v0.json, from exp012_forward_vmap fetch --new at or after
     2026-10-16T00Z);
  null: neither (section 4's missing-V0 rule).
"All null" is not this branch: Am.1 names sources 2 and 3 for it, section 4's universe is "the first V-range pool after
`complete`", and its missing-V0 rule covers a pool that "cannot be decoded or fetched". An all-null map would also make every pool
with a known out-of-range V0 a candidate. The mode refuses unless p5/cross_source.json records line_a_pass false (a passed line A
keeps the ev map, so the branch is never chosen by effect), gettx_v.jsonl hashes to the sha256 cross_source.json recorded, and
account_v0.json exists (source 3 is part of the order, so this runs after `boostfloor_inputs account`). The extract is then re-run
into a new dir with this map, before classify and P6.

Pools with no source 2 or 3 record (`null_no_source`, e.g. pools the ev-based extract left out of the universe) become null-V
candidates in the rebuilt extract. Before classify and P6, P5's s0 fetch and `account` must run again on the rebuilt extract into a
new P5 dir, and gettx must be re-run on it. Otherwise those pools take section 4's missing-V rule. This is a manager step and is not
done here.

FIRST PRINT UNCERTAIN. A base hour that is not usable (forward_v_join.hour_state, strict=False, as the join reads base), that stops on
a read error, or that holds an unreadable line can hide a pool's first print. "Uncertain" means first seen in such a (non-ok) hour or
in the hour right after one, or a pool with a row that has no integer slot. A pool first seen two or more hours after the first such
hour keeps its V: the extractor's hole rule (h5_forward_extract.HOLE_RULE) drops a mint whose migration hour or the next hour is
unusable, and a migration pool cannot print before its `complete`, so a pool the extract keeps does not have its first print hidden
further back than the hour before the first one seen. ev gives an uncertain pool null; gettx skips source 2 (a print's V) and still
takes source 3 (the pool's V0). Counted as first_print_uncertain. The meta's first_gap_hour is the first non-ok hour and nothing more.

SEAL. Both modes refuse before opening anything unless the EXP-012 FINAL marker is in the external FINAL ledger
(tools.forward_v_join.final_marker, the check the join and the extractor make). A path that names a sealed or forbidden block
(h5_forward_extract.FORBIDDEN_PARTS) is refused. Nothing here computes a trigger, fill, exit or P&L, or opens an extract output.
stdout gets counts and sha256s only; pool ids, signatures and V values stay in the files. Writes OUT and OUT.meta.json (counts, input
sha256s, blobs), each a new file (O_EXCL), never inside the walk dir. Exit 0 written, 2 refused.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools import forward_v_join as J
from tools import h5_forward_extract as X

REPO = Path(__file__).resolve().parents[1]
# EXP-024 section 3, Look 1: the extractor's forward hours, which are boostfloor_read.READ_HOURS. Constants only (tests patch them).
FWD_DIR = X.FWD_DIR
FWD_FROM, FWD_TO = X.FWD_FROM, X.FWD_TO
N_HOURS = 146
FINAL_LEDGER = X.FINAL_LEDGER
V_FIELD = J.V_FIELD
VENUE = J.VENUE_PUMPSWAP
RECORDED_BLOBS = ("tools/h5_forward_vmap.py", "tools/forward_v_join.py", "tools/h5_forward_extract.py",
                  "tools/boostfloor_inputs.py", "tools/boostfloor_read.py")
MARKER_FIELDS = ("utc_time", "written_utc", "ts", "final", "rows_sha256", "lock_sha256")


class Refused(Exception):
    """A refusal before or instead of a run. Exit 2."""


# ---- hours and paths -------------------------------------------------------------------------------------------------

def look1_hours() -> list[str]:
    hours = J.hour_list(FWD_FROM, FWD_TO)
    if len(hours) != N_HOURS:
        raise Refused(f"[{FWD_FROM}, {FWD_TO}) is {len(hours)} hours, not the {N_HOURS} Look 1 hours")
    return hours


def check_path(path: Path) -> None:
    X.check_source(path)


def check_out(out: Path) -> Path:
    out = Path(out)
    check_path(out)
    meta = meta_path(out)
    for p in (out, meta):
        if p.exists():
            raise Refused(f"{p} exists; this tool never overwrites")
        if J._inside(p, FWD_DIR):
            raise Refused("an output path is inside the walk dir; the sealed dirs are never written")
    return out


def meta_path(out: Path) -> Path:
    return out.with_name(out.name + ".meta.json")


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


# ---- first prints (forward-1002, the 146 hours) ------------------------------------------------------------------------

def _int(x: Any) -> int | None:
    return x if type(x) is int else None


@dataclass
class First:
    order: tuple[Any, ...]
    hour: str
    key: tuple[int, str, int] | None  # (slot, signature, event_index), the join's key; None: unkeyed row
    content: list[Any] | None  # [slot, mint, sol_lamports, token_raw, quote_reserve, base_reserve], P5's content key


@dataclass
class Scan:
    first: dict[str, First] = field(default_factory=dict)
    pools: set[str] = field(default_factory=set)
    unordered: set[str] = field(default_factory=set)
    states: dict[str, str] = field(default_factory=dict)
    gap_at: int | None = None  # index of the first hour that is not fully read (the meta's first_gap_hour only)
    bad_at: set[int] = field(default_factory=set)  # indexes of every hour that is not fully read
    pumpswap_rows: int = 0
    pumpswap_no_pool: int = 0

    def uncertain(self, pool: str) -> bool:
        """First seen in a non-ok hour or in the hour right after one (see the docstring), or no integer slot on some row."""
        f = self.first.get(pool)
        return f is None or pool in self.unordered or f.order[5] in self.bad_at or (f.order[5] - 1) in self.bad_at


def _content(row: dict[str, Any]) -> list[Any] | None:
    vals = [row.get(k) for k in ("slot", "mint", "sol_lamports", "token_raw", "quote_reserve", "base_reserve")]
    if not isinstance(vals[1], str) or any(_int(v) is None for i, v in enumerate(vals) if i != 1):
        return None
    return vals


def scan_first_prints(base_dir: Path, hours: Sequence[str]) -> Scan:
    s = Scan()
    for hi, h in enumerate(hours):
        path, st = J.hour_state(base_dir, h, strict=False)
        if path is None:
            s.states[h] = "base_" + st
        else:
            rd = J.RowReader(path)
            try:
                for ln, row in enumerate(rd):
                    if row.get("venue") != VENUE:
                        continue
                    s.pumpswap_rows += 1
                    pool = row.get("pool")
                    if not isinstance(pool, str):
                        s.pumpswap_no_pool += 1
                        continue
                    s.pools.add(pool)
                    slot = _int(row.get("slot"))
                    if slot is None:
                        s.unordered.add(pool)
                        continue
                    tx, ei = _int(row.get("tx_index")), _int(row.get("event_index"))
                    o = (slot, tx is None, tx or 0, ei is None, ei or 0, hi, ln)
                    cur = s.first.get(pool)
                    if cur is None or o < cur.order:
                        s.first[pool] = First(o, h, J.row_key(row), _content(row))
                s.states[h] = "ok" if rd.bad_lines == 0 else "bad_lines"
            except J.HourReadError:
                s.states[h] = "read_error"
        if s.states[h] != "ok":
            s.bad_at.add(hi)
            if s.gap_at is None:
                s.gap_at = hi
    return s


# ---- ev (Am.1 source 1) ------------------------------------------------------------------------------------------------

def check_join(vjoin: Path, hours: Sequence[str]) -> tuple[dict[str, Path], set[str], dict[str, str]]:
    """({hour: v file}, usable hours, input sha256s). Refuses a join output that is not the Look 1 join of this base dir."""
    rep_path = vjoin / "join-report.json"
    if not rep_path.is_file():
        raise Refused(f"{rep_path} is missing: run forward_v_join join --from {FWD_FROM} --to {FWD_TO} --out-dir {vjoin}")
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    if rep.get("tool") != "tools/forward_v_join.py" or rep.get("from") != FWD_FROM or rep.get("to") != FWD_TO:
        raise Refused(f"join-report.json is not the Look 1 join [{FWD_FROM}, {FWD_TO})")
    if [x.get("hour") for x in rep.get("hours") or []] != list(hours):
        raise Refused("join-report.json does not list exactly the Look 1 hours")
    if not isinstance(rep.get("base"), str) or Path(rep["base"]).resolve() != Path(FWD_DIR).resolve():
        raise Refused("join-report.json was not run on this forward-1002 dir")
    pins = rep.get("decoder_pins") or []
    if not pins or not all(isinstance(p, dict) and p.get("ok") is True for p in pins):
        raise Refused("join-report.json does not record all decoder pins ok")
    shas = {Path(k).name: v for k, v in (rep.get("outputs_sha256") or {}).items()}
    usable = {x["hour"] for x in rep["hours"] if x.get("usable") is True}
    files: dict[str, Path] = {}
    for h in hours:
        p = J.hour_file_named(vjoin, f"v-{h}")
        if p is None:
            if h in usable and f"v-{h}.jsonl.zst" in shas:
                raise Refused(f"v file of usable hour {h} is missing")
            continue
        if h not in usable:
            raise Refused(f"a v file exists for hour {h}, which the join refused")
        if shas.get(p.name) != J.sha256_file(p):
            raise Refused(f"{p.name} does not hash to the sha256 in join-report.json")
        files[h] = p
    # a v-<hour> file is stray unless it is the very file read for that hour (an extra v-<hour>.jsonl beside the .zst is refused)
    stray = sorted(p.name for p in vjoin.glob("v-*") if p.name[2:15] not in files or p.name != files[p.name[2:15]].name)
    if stray:
        raise Refused(f"{len(stray)} v file(s) in {vjoin} are not the Look 1 join's")
    inputs = {"join-report.json": J.sha256_file(rep_path), "v_files": str(len(files))}
    fb = vjoin / "fallback.jsonl"
    if fb.is_file():
        if shas.get(fb.name) not in (None, J.sha256_file(fb)):
            raise Refused("fallback.jsonl does not hash to the sha256 in join-report.json")
        inputs["fallback.jsonl"] = J.sha256_file(fb)
    return files, usable, inputs


def build_ev(scan: Scan, files: dict[str, Path], usable: set[str]) -> tuple[dict[str, int | None], dict[str, int]]:
    need: dict[str, dict[tuple[int, str, int], str]] = {}
    reason: dict[str, str] = {}
    for pool in scan.pools:
        f = scan.first.get(pool)
        if scan.uncertain(pool):
            reason[pool] = "null_first_print_uncertain"
        elif f is None or f.key is None:
            reason[pool] = "null_first_print_unkeyed"
        elif f.hour not in usable:
            reason[pool] = "null_join_hour_refused"
        else:
            need.setdefault(f.hour, {})[f.key] = pool
    found: dict[str, int] = {}
    conflict: set[str] = set()
    for h, keys in sorted(need.items()):
        p = files.get(h)
        if p is None:
            continue
        rd = J.RowReader(p)
        for rec in rd:
            k = J.row_key(rec)
            if k is None or k not in keys:
                continue
            v = rec.get(V_FIELD)
            pool = keys[k]
            if type(v) is int:
                if pool in found and found[pool] != v:
                    conflict.add(pool)
                found[pool] = v
        if rd.bad_lines:
            raise Refused(f"v file of hour {h} has unreadable lines")
    vmap: dict[str, int | None] = {}
    counts: dict[str, int] = {}
    for pool in sorted(scan.pools):
        if pool in reason:
            r, v = reason[pool], None
        elif pool in conflict:
            r, v = "null_ev_conflict", None
        elif pool in found:
            r, v = "ev", found[pool]
        else:
            r, v = "null_no_joined_v", None
        vmap[pool] = v
        counts[r] = counts.get(r, 0) + 1
    return vmap, counts


# ---- gettx (line A failed: Am.1 sources 2, then 3) ---------------------------------------------------------------------

def check_p5(p5: Path) -> tuple[dict[tuple[int, str, int], list[dict[str, Any]]], dict[str, int], dict[str, str]]:
    cs_path, gt_path, ac_path = p5 / "cross_source.json", p5 / "gettx_v.jsonl", p5 / "account_v0.json"
    if not cs_path.is_file():
        raise Refused(f"{cs_path} is missing: P5 has not run, so line A has not failed")
    cs = json.loads(cs_path.read_text(encoding="utf-8"))
    if cs.get("line_a_pass") is True:
        raise Refused("P5 line A passed: Am.1 source 1 stands and the ev vmap is the one the extract reads")
    if cs.get("line_a_pass") is not False:
        raise Refused("cross_source.json records no line_a_pass")
    if not gt_path.is_file() or J.sha256_file(gt_path) != cs.get("gettx_sha256"):
        raise Refused("gettx_v.jsonl is missing or does not hash to the sha256 cross_source.json recorded")
    if not ac_path.is_file():
        raise Refused(f"{ac_path} is missing: run boostfloor_inputs account first (source 3 is part of the Am.1 order)")
    recs: dict[tuple[int, str, int], list[dict[str, Any]]] = {}
    for ln in gt_path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            k = J.row_key(r)
            if k is not None:
                recs.setdefault(k, []).append(r)
    acct_raw = json.loads(ac_path.read_text(encoding="utf-8"))
    if not isinstance(acct_raw, dict):
        raise Refused("account_v0.json is not a {pool: V0} object")
    acct = {str(k): v for k, v in acct_raw.items() if type(v) is int}
    inputs = {"cross_source.json": J.sha256_file(cs_path), "gettx_v.jsonl": J.sha256_file(gt_path),
              "account_v0.json": J.sha256_file(ac_path)}
    return recs, acct, inputs


def gettx_v(rs: list[dict[str, Any]] | None, f: First, pool: str) -> int | None:
    if not rs or len(rs) != 1:
        return None
    r = rs[0]
    d = r.get("decoded") or {}
    v = d.get(V_FIELD)
    if (r.get("status") == "ok" and r.get("fields_equal") is True and type(v) is int and d.get("pool") == pool
            and f.content is not None and r.get("key") == f.content):
        return v
    return None


def build_gettx(scan: Scan, recs: dict[tuple[int, str, int], list[dict[str, Any]]],
                acct: dict[str, int]) -> tuple[dict[str, int | None], dict[str, int]]:
    vmap: dict[str, int | None] = {}
    counts: dict[str, int] = {}
    for pool in sorted(scan.pools):
        f = scan.first.get(pool)
        v, r = None, "null_no_source"
        if not scan.uncertain(pool) and f is not None and f.key is not None:
            rs = recs.get(f.key)
            v = gettx_v(rs, f, pool)
            if v is not None:
                r = "gettx"
            elif rs:
                counts["gettx_record_rejected"] = counts.get("gettx_record_rejected", 0) + 1
        if v is None and pool in acct:
            v, r = acct[pool], "account"
        if v is None and scan.uncertain(pool):
            r = "null_first_print_uncertain"
        vmap[pool] = v
        counts[r] = counts.get(r, 0) + 1
    return vmap, counts


# ---- the run -----------------------------------------------------------------------------------------------------------

def run(mode: str, src: Path, out: Path) -> dict[str, Any]:
    marker = J.final_marker(Path(FINAL_LEDGER))  # first: nothing is opened before the FINAL
    hours = look1_hours()
    for p in (src, FWD_DIR):
        check_path(Path(p))
    out = check_out(out)
    if mode == "ev":
        if Path(src).name != "vjoin":
            raise Refused("--vjoin must be named vjoin and sit inside the P5 dir (p5/vjoin), so that the line A check can read "
                          "p5/cross_source.json")
        cs = Path(src).parent / "cross_source.json"  # Layout: p5/vjoin next to p5/cross_source.json
        if cs.is_file() and json.loads(cs.read_text(encoding="utf-8")).get("line_a_pass") is not True:
            raise Refused("P5 line A did not pass: Am.1 forbids forward-1002ev for Look 1; rebuild with `gettx`")
        files, usable, inputs = check_join(Path(src), hours)
        scan = scan_first_prints(Path(FWD_DIR), hours)
        vmap, counts = build_ev(scan, files, usable)
        source = "Am.1 source 1: forward-1002ev through forward_v_join join, at the first print"
    elif mode == "gettx":
        recs, acct, inputs = check_p5(Path(src))
        scan = scan_first_prints(Path(FWD_DIR), hours)
        vmap, counts = build_gettx(scan, recs, acct)
        source = ("Am.1 line A failed: no ev; source 2 getTransaction (P5 gettx_v.jsonl) at the first print, "
                  "then source 3 the pool account V0 (account_v0.json), else null")
    else:
        raise Refused(f"unknown mode {mode!r}")
    states: dict[str, int] = {}
    for st in scan.states.values():
        states[st] = states.get(st, 0) + 1
    data = (json.dumps({"v": dict(sorted(vmap.items()))}, sort_keys=True, separators=(",", ":")) + "\n").encode()
    write_new(out, data)
    vmap_sha = J.sha256_file(out)
    meta = {
        "tool": "tools/h5_forward_vmap.py", "mode": mode, "v_source": source, "hours": [FWD_FROM, FWD_TO], "n_hours": len(hours),
        "base": str(FWD_DIR), "base_hour_states": scan.states, "base_state_counts": states,
        "first_gap_hour": None if scan.gap_at is None else hours[scan.gap_at],
        "pools": len(vmap), "pools_with_v": sum(v is not None for v in vmap.values()),
        "pools_null": sum(v is None for v in vmap.values()), "counts": dict(sorted(counts.items())),
        "pumpswap_rows": scan.pumpswap_rows, "pumpswap_rows_no_pool": scan.pumpswap_no_pool,
        "inputs_sha256": inputs, "vmap": str(out), "vmap_sha256": vmap_sha,
        "final_marker": {k: marker.get(k) for k in MARKER_FIELDS if k in marker},
        "blobs": {rel: (X.git_blob_sha(REPO / rel) if (REPO / rel).is_file() else None) for rel in RECORDED_BLOBS},
        "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    mp = meta_path(out)
    write_new(mp, (json.dumps(meta, indent=1, sort_keys=True) + "\n").encode())
    meta["meta_sha256"] = J.sha256_file(mp)
    return meta


def summary(m: dict[str, Any]) -> str:
    return (f"h5 forward vmap ({m['mode']}): hours {m['n_hours']}, base {json.dumps(m['base_state_counts'], sort_keys=True)}, "
            f"pools {m['pools']}, with V {m['pools_with_v']}, null {m['pools_null']}, counts {json.dumps(m['counts'], sort_keys=True)}, "
            f"inputs {json.dumps(m['inputs_sha256'], sort_keys=True)}, vmap sha256 {m['vmap_sha256']}, meta sha256 {m['meta_sha256']}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ev", help="Am.1 source 1: V at each pool's first print from forward_v_join join's output")
    e.add_argument("--vjoin", type=Path, required=True, help="the join's --out-dir (join-report.json, v-<hour>.jsonl.zst)")
    e.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("gettx", help="line A failed: Am.1 sources 2 then 3, no ev")
    g.add_argument("--p5", type=Path, required=True, help="the P5 dir (cross_source.json, gettx_v.jsonl, account_v0.json)")
    g.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        m = run(a.cmd, a.vjoin if a.cmd == "ev" else a.p5, a.out)
    except (Refused, J.Refused, X.Refused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # the class only: a message could carry a row
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(summary(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
