#!/usr/bin/env python3
"""arena_g4v1_equiv: entry-decision equivalence of the G4-FLOORDIP v1 streaming port (tools/arena_rule_g4v1.py) and the kit's G4 v1 entry list. PAPER only. No key, no network.

    python -m tools.arena_g4v1_equiv --hours tape-<hour>.jsonl.zst ... --kit-csv kit.csv --out DIR

HOURS: arena-format hours (tools/arena_clean_adapter.py output for an exploration block; the pattern of tools/arena_port_check.py, #765). The port runs in HostV2 replay over exactly
those files (rule default table mode `complete`, a decision-only engine: nothing is priced) and its `entry` rows are the port's decisions.
KIT CSV: columns `pool,T` (T = the hour boundary in epoch seconds). It is the kit's own G4 v1 book-entry list (hunt3 exit-upgrade/work/{aug,sep}_book_entries.parquet, the list the hunt26
BASE exit screen used), converted to CSV and restricted BY THE CALLER to what the replayed hours can contain (pools whose `complete` time lies inside the hours, T inside the hours): this tool
does not know the kit's migration table. Compared per (pool, hour): matched, only_kit, only_port. Counts and the first 20 differences by (hour, pool) only; no price, no outcome.
The kit list is a BOOK (one open pool, kit exit times), the port's TP / time exit timing differs slightly, so a re-entry hour can differ without the entry rule differing: reported, not hidden.
Refusals: the same word list as arena_port_check (forward-, truth-, fresh-08, exp011-, probe, ...), an hour inside [2026-10-02T10, 2026-10-16T01), an --out inside /data/mal/arena or /data/mal/clean-view.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from tools import arena_port_check as PC
from tools import arena_rules as R
from tools import arena_rules_v2 as V2

HOUR_MS = 3_600_000


def read_kit(path: str) -> set[tuple[str, int]]:
    PC.check_path_words(path, "kit csv")
    with open(path, newline="") as fh:
        return {(r["pool"], int(float(r["T"]))) for r in csv.DictReader(fh)}


def compare(kit: set[tuple[str, int]], port: set[tuple[str, int]], limit: int = 20) -> dict[str, Any]:
    both, ok, op = kit & port, sorted(kit - port, key=lambda x: (x[1], x[0])), sorted(port - kit, key=lambda x: (x[1], x[0]))
    return {"n_kit": len(kit), "n_port": len(port), "matched": len(both), "only_kit": len(ok), "only_port": len(op),
            "first_only_kit": [{"hour_s": t, "pool": p} for p, t in ok[:limit]], "first_only_port": [{"hour_s": t, "pool": p} for p, t in op[:limit]]}


def port_entries(files: Sequence[tuple[Any, Path]], scratch: Path) -> tuple[set[tuple[str, int]], dict[str, Any]]:
    from tools.arena_rule_g4v1 import make_rule

    R.Host = V2.HostV2  # as the FLOOR check: the port-check builds its host as R.Host(cfg, rules, state, engine)
    scratch.mkdir(parents=True, exist_ok=True)
    rep = PC.port_replay([make_rule()], files, scratch)
    got = {(row["pool"], (int(row["ms"]) // HOUR_MS) * 3600) for _, row in rep["entries"]}
    if len(got) != len(rep["entries"]):
        rep["duplicate_hour_entries"] = len(rep["entries"]) - len(got)
    return got, rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", nargs="+", required=True)
    ap.add_argument("--kit-csv", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        files = PC.check_hour_files(a.hours)
        out = PC.check_out_dir(a.out)
        kit = read_kit(a.kit_csv)
    except PC.PortCheckRefused as e:
        print(f"REFUSED {e}", file=sys.stderr)
        return 2
    got, rep = port_entries(files, out / "scratch")
    res = compare(kit, got)
    res.update({"hours": len(files), "hook_errors": len(rep["hook_errors"]), "fatal": rep["fatal"], "log_md5": rep["md5"], "counters": rep["counters"],
                "duplicate_hour_entries": rep.get("duplicate_hour_entries", 0), "kit_csv": a.kit_csv})
    (out / "g4v1_equiv.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    print(json.dumps({k: res[k] for k in ("n_kit", "n_port", "matched", "only_kit", "only_port", "hook_errors", "fatal")}), flush=True)
    return 0 if res["only_kit"] == 0 and res["only_port"] == 0 and not res["hook_errors"] and not res["fatal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
