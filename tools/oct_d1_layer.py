#!/usr/bin/env python3
"""October D1 (pre-graduation) research data layer, built ONLY through the F1 role `oct-pregrad-d1`.

    python tools/oct_d1_layer.py --dataset forward-1002 --start 2026-10-02T15 --end 2026-10-16T01 --out <dir>

`--end` is exclusive. Interpreter: one with pyarrow and duckdb (`/data/mal/audit-1008/venv/bin/python`; the
`/data/mal/venv` interpreter has no pyarrow). If `zstandard` is missing, hour files are decoded with pyarrow.

What it reads (nothing else): `trades/` and `creates/` hour files of the released dataset through
`tools.mal_catalog_october` (every parsed line goes through `mask_row`; trade rows go through `guarded_rows`, so every
D1 verdict is applied: not a trade, pool venue, at/after the migrate tx, after the `complete` trade), and `migrations/`
hour files only through `migrate_positions` (mint, slot, event_index). Pool, migration-onward and PumpSwap rows are never
kept; a kept row of any venue but `pump_bonding` aborts the run (exit 2).

`check_read` is called before any file is opened or hashed. It demands a `migrate_pos` argument, which is only known
after `migrate_positions` has run (and that function has its own gate); the same dict object is passed first
(empty) and filled afterwards, so the check authorises the paths and the latch is taken before any open.

The `complete` trade. D1 may not open migrations/ rows beyond (mint, slot, event_index), and the mask drops everything
but identities from non-trade rows, so `complete` is found on the bonding trades themselves: the first buy whose
post-trade virtual token reserve (`base_reserve`) is at or below `COMPLETE_VIRTUAL_TOKEN_RESERVE_RAW`
(1.073e15 initial virtual minus 7.931e14 real tokens for sale = 2.799e14 raw). Rows after that trade are dropped by
`guarded_rows` (`after_complete`). The constant is a pump.fun curve constant and is NOT verified against any October
file by this tool or its tests; the manifest carries `complete_detection` counts (exact vs strictly below the
constant) and an aggregate cross-check against the migrate map so the manager can see a mismatch.

Outputs (Parquet, zstd), mirroring /data/mal/hunt-shared minus every column the D1 mask forbids: no PumpSwap column,
no V, no pool, no migration column, no fee column, no creator/name/symbol/mayhem (the D1 mask keeps identities only on
create rows), no `fm_*`. Times are `event_ts * 1000` (whole seconds; `block_time` is not in the D1 mask).

    tokens_d1.parquet, bars_1m/<YYYY-MM-DD>.parquet, wallet_mint_d1.parquet, MANIFEST.json

Joining these outputs to any D0, D2 or D3 table of the same mint stays forbidden while a seal on that pool holds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import mal_catalog as mc  # noqa: E402
from tools import mal_catalog_october as oc  # noqa: E402

ROLE = oc.ROLE_OCT_D1
# pump.fun curve: initial virtual token reserve 1.073e15 raw, 7.931e14 sold on the curve; the completing buy leaves
# real_token_reserves = 0, i.e. a virtual token reserve of 2.799e14. Unverified against October data (see module doc).
COMPLETE_VIRTUAL_TOKEN_RESERVE_RAW = 279_900_000_000_000
# Raw columns this tool names in check_read (all on the D1 allowlist).
INPUT_COLUMNS = ("v", "type", "venue", "mint", "trader", "slot", "signature", "event_index", "event_ts",
                 "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "zero_sol")
# Output names refused on top of the module's own D1 mask (it is an allowlist for raw columns, a regex for names).
_EXTRA_FORBIDDEN_OUT = ("pumpswap", "ps_", "v_lamports", "v0", "grad", "pool", "creator", "name", "symbol", "mayhem",
                        "fee", "fm_", "migrat", "synthetic", "boost")
SKIP_KEYS = ("not_trade", "pool_venue", "after_migrate", "after_complete")
STAGE_COLUMNS = ("mint", "trader", "is_buy", "lam", "tok", "q", "b", "slot", "ei", "ord", "seq", "ms", "price")
CREATE_FIELDS = ("mint", "slot", "event_index", "event_ts")


class LayerRefused(Exception):
    """A D1 layer invariant failed (non-bonding row kept, forbidden column, missing input...)."""


class _Positions(Mapping):
    """mint -> (slot, event_index) | None; every mint is 'present' (absent = None).

    migrate: built by `migrate_positions` from the full tape through the end of the released window, so absent means
    never migrated. complete: filled while streaming hour by hour. Only this class ever answers `in`; the fail-closed
    'mint missing from the map' check of `_d1_verdict` is deliberately replaced by this documented rule."""

    def __init__(self, data: dict[str, tuple[int, int]] | None = None) -> None:
        self.data: dict[str, tuple[int, int]] = dict(data or {})

    def __contains__(self, key: object) -> bool:
        return True

    def __getitem__(self, key: str) -> tuple[int, int] | None:
        return self.data.get(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


def _lines_pyarrow(path: Path) -> Iterator[str]:
    import pyarrow as pa

    with pa.OSFile(str(path), "rb") as f:
        s = pa.CompressedInputStream(f, "zstd") if path.suffix == ".zst" else f
        buf = b""
        while True:
            chunk = s.read(1 << 20)
            if not chunk:
                break
            parts = (buf + chunk).split(b"\n")
            buf = parts.pop()
            for p in parts:
                yield p.decode("utf-8") + "\n"
        if buf:
            yield buf.decode("utf-8")


def _install_reader() -> str:
    """Use the module's zstandard reader when importable, else pyarrow (the module-level `_lines` is what
    `migrate_positions` calls, so one reader serves both)."""
    try:
        import zstandard  # noqa: F401

        return "zstandard"
    except ImportError:
        oc._lines = _lines_pyarrow
        return "pyarrow"


def _sha256(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _hours(start: str, end: str) -> list[str]:
    h, e, out = mc._parse_hour(start), mc._parse_hour(end), []
    while h < e:
        out.append(mc._fmt_hour(h))
        h += timedelta(hours=1)
    return out


def _hour_path(root: Path, dataset: str, kind: str, hour: str) -> Path:
    return root / dataset / kind / f"{kind}-{hour}.jsonl.zst"


def check_output_columns(columns: list[str]) -> None:
    """Every output column must pass the module's D1 name mask and the extra list above."""
    for c in columns:
        if oc._forbidden(ROLE, c) or any(x in c.lower() for x in _EXTRA_FORBIDDEN_OUT):
            raise LayerRefused(f"output column {c!r} is forbidden for the D1 layer")


def _git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _json_rows(path: Path) -> Iterator[dict[str, Any]]:
    for n, line in enumerate(oc._lines(path), 1):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except ValueError:
            raise LayerRefused(f"{path.name}: line {n} is not JSON (fail closed)") from None


def _stage_row(r: dict[str, Any], seq: int) -> dict[str, Any] | None:
    """Masked bonding trade -> stage row, or None if the reserves cannot give a price (hunt-shared rule)."""
    if r.get("venue") != oc.BONDING_VENUE:
        raise LayerRefused(f"a row of venue {r.get('venue')!r} would be kept (only {oc.BONDING_VENUE} is allowed)")
    mint = r.get("mint")
    if not isinstance(mint, str) or not mint:
        raise LayerRefused("a bonding row has no mint (fail closed)")
    q, b = r.get("quote_reserve"), r.get("base_reserve")
    if q is None or b is None or b <= 0:
        return None
    ei = int(r["event_index"])
    return {"mint": mint, "trader": r.get("trader"), "is_buy": r.get("side") == "buy", "lam": int(r["sol_lamports"]),
            "tok": int(r["token_raw"]), "q": int(q), "b": int(b), "slot": int(r["slot"]), "ei": ei,
            "ord": int(r["slot"]) * 100_000 + min(ei, 99_999), "seq": seq,
            "ms": int(r["event_ts"]) * 1000, "price": int(q) / (int(b) * 1000.0)}


def _merge_skips(into: dict[str, int], add: Mapping[str, int]) -> None:
    for k, v in add.items():
        into[k] = into.get(k, 0) + v


def run(args: argparse.Namespace) -> dict[str, Any]:
    import duckdb
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    dataset, start, end = args.dataset, args.start, args.end
    out = Path(args.out)
    reader = _install_reader()
    hours = _hours(start, end)
    if not hours:
        raise LayerRefused("empty hour range")
    root = oc.BLOCKS_ROOT
    mig_hours = _hours(start, oc.FORWARD_END_EXCLUSIVE)
    trades_p = [_hour_path(root, dataset, "trades", h) for h in hours]
    creates_p = [_hour_path(root, dataset, "creates", h) for h in hours]
    mig_p = [_hour_path(root, dataset, "migrations", h) for h in mig_hours]
    missing = [str(p) for p in trades_p + creates_p + mig_p if not os.path.exists(p)]
    if missing:
        raise LayerRefused(f"{len(missing)} input hour files are missing, e.g. {missing[0]} (fail closed)")

    blocks = mc.parse_ledger((REPO_ROOT / "docs" / "HOLDOUT_LEDGER.md").read_text(encoding="utf-8"))
    gates = oc.resolved_gates()
    print("resolved_gates", json.dumps(gates), flush=True)
    mig_filled: dict[str, tuple[int, int]] = {}
    # check_read BEFORE any open; the migrate map is filled below into the same dict object.
    ok, reasons = oc.check_read(blocks, ROLE, dataset, start, end, INPUT_COLUMNS,
                                paths=[*trades_p, *creates_p], migrate_pos=mig_filled)
    if not ok:
        raise LayerRefused("check_read refused: " + "; ".join(reasons))
    check_output_columns(["mint", "create_ms", "create_slot", "create_event_index", "has_create", "reached_complete",
                          "complete_ms", "complete_slot", "complete_event_index", "bc_first_ms", "bc_last_ms",
                          "bc_buy_sol", "bc_sell_sol", "bc_n_trades", "bc_n_traders"])

    out.mkdir(parents=True, exist_ok=True)
    stage = out / "tmp" / "stage"
    shutil.rmtree(out / "tmp", ignore_errors=True)
    stage.mkdir(parents=True)
    inputs: list[dict[str, Any]] = []

    def hashed(p: Path, kind: str, hour: str) -> None:
        sha, n = _sha256(p)
        inputs.append({"path": str(p), "kind": kind, "hour": hour, "sha256": sha, "bytes": n})

    # migrate map: the only use of migrations/ (mint, slot, event_index), from the full tape through 10-16T01
    for p, h in zip(mig_p, mig_hours):
        hashed(p, "migrations", h)
    mig_filled.update(oc.migrate_positions(blocks, dataset, start, oc.FORWARD_END_EXCLUSIVE, mig_p))
    migrate_pos = _Positions(mig_filled)
    complete_pos = _Positions()
    complete_info: dict[str, tuple[int, int, int]] = {}  # mint -> (slot, ei, ms)
    det = {"exact": 0, "below": 0}

    skipped: dict[str, int] = {k: 0 for k in SKIP_KEYS}
    coverage: list[dict[str, Any]] = []
    creates: dict[str, tuple[int, int, int]] = {}  # mint -> earliest (slot, ei, event_ts)
    n_stage, n_bad_reserves, n_create_other = 0, 0, 0
    for idx, h in enumerate(hours):
        tp, cp = trades_p[idx], creates_p[idx]
        hashed(tp, "trades", h)
        hashed(cp, "creates", h)
        n_creates = 0
        for r in _json_rows(cp):  # create rows: mask_row only (identities; creator/name/symbol are not in the mask)
            m = oc.mask_row(ROLE, r)
            if m.get("type") != "create" or not isinstance(m.get("mint"), str):
                n_create_other += 1
                continue
            n_creates += 1
            key = (int(m["slot"]), int(m.get("event_index") or 0), int(m["event_ts"]))
            if m["mint"] not in creates or key < creates[m["mint"]]:
                creates[m["mint"]] = key
        # pass A: guarded + masked rows of the hour (live migrate/complete maps), buffered
        skA: dict[str, int] = {}
        buf = list(oc.guarded_rows(ROLE, _json_rows(tp), migrate_pos, complete_pos, None, skA))
        # complete detection on masked rows only
        found: dict[str, tuple[int, int, int, int]] = {}
        for r in buf:
            b = r.get("base_reserve")
            if r.get("side") == "buy" and isinstance(b, int) and b <= COMPLETE_VIRTUAL_TOKEN_RESERVE_RAW:
                pos = (int(r["slot"]), int(r["event_index"]), int(r["event_ts"]) * 1000, b)
                m = r["mint"]
                if m not in found or pos[:2] < found[m][:2]:
                    found[m] = pos
        for m, pos in found.items():
            if m in complete_pos.data:
                continue
            complete_pos.data[m] = pos[:2]
            complete_info[m] = pos[:3]
            det["exact" if pos[3] == COMPLETE_VIRTUAL_TOKEN_RESERVE_RAW else "below"] += 1
        # pass B: the same verdicts again with the complete positions known (drops rows after `complete`)
        skB: dict[str, int] = {}
        kept = list(oc.guarded_rows(ROLE, buf, migrate_pos, complete_pos, None, skB))
        _merge_skips(skipped, skA)
        _merge_skips(skipped, skB)
        recs = []
        for i, r in enumerate(kept):
            s = _stage_row(r, idx * 10_000_000 + i)
            if s is None:
                n_bad_reserves += 1
            else:
                recs.append(s)
        if recs:
            tb = pa.table({c: [x[c] for x in recs] for c in STAGE_COLUMNS})
            pq.write_table(tb, stage / f"{h}.parquet", compression="zstd", compression_level=3)
        n_stage += len(recs)
        coverage.append({"hour": h, "bonding_prints": len(recs), "mints_with_prints": len({x["mint"] for x in recs}),
                         "creates": n_creates, "completes": len(found)})
        print(f"hour {h} prints={len(recs)} creates={n_creates} completes={len(found)}", flush=True)

    con = duckdb.connect(str(out / "tmp" / "d1.duckdb"))
    con.execute(f"SET memory_limit='{args.memory_gb}GB'")
    con.execute(f"SET threads={args.threads}")
    con.execute(f"SET temp_directory='{out / 'tmp' / 'duck'}'")
    con.execute("SET preserve_insertion_order=false")
    outputs: dict[str, Any] = {}

    def write(table: "pa.Table", path: Path) -> None:
        check_output_columns(table.column_names)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        pq.write_table(table, tmp, compression="zstd", compression_level=3, row_group_size=1_000_000)
        os.replace(tmp, path)
        sha, n = _sha256(path)
        outputs[str(path.relative_to(out))] = {"rows": table.num_rows, "sha256": sha, "bytes": n}

    have_stage = n_stage > 0
    if have_stage:
        con.execute(f"CREATE VIEW stage AS SELECT * FROM read_parquet('{stage}/*.parquet')")
    else:
        con.execute("CREATE TABLE stage (mint VARCHAR, trader VARCHAR, is_buy BOOLEAN, lam BIGINT, tok BIGINT, q BIGINT, "
                    "b BIGINT, slot BIGINT, ei BIGINT, ord BIGINT, seq BIGINT, ms BIGINT, price DOUBLE)")

    # tokens_d1
    ct = pa.table({"mint": list(creates), "create_slot": [v[0] for v in creates.values()],
                   "create_event_index": [v[1] for v in creates.values()],
                   "create_ms": [v[2] * 1000 for v in creates.values()]},
                  schema=pa.schema([("mint", pa.string()), ("create_slot", pa.int64()),
                                    ("create_event_index", pa.int64()), ("create_ms", pa.int64())]))
    cm = pa.table({"mint": list(complete_info), "complete_slot": [v[0] for v in complete_info.values()],
                   "complete_event_index": [v[1] for v in complete_info.values()],
                   "complete_ms": [v[2] for v in complete_info.values()]},
                  schema=pa.schema([("mint", pa.string()), ("complete_slot", pa.int64()),
                                    ("complete_event_index", pa.int64()), ("complete_ms", pa.int64())]))
    con.register("creates_t", ct)
    con.register("complete_t", cm)
    tokens = con.execute("""
        WITH bc AS (SELECT mint, min(ms) AS bc_first_ms, max(ms) AS bc_last_ms,
                           sum(lam) FILTER (WHERE is_buy) / 1e9 AS bc_buy_sol, sum(lam) FILTER (WHERE NOT is_buy) / 1e9 AS bc_sell_sol,
                           count(*)::BIGINT AS bc_n_trades, count(DISTINCT trader)::BIGINT AS bc_n_traders
                    FROM stage GROUP BY mint),
             allm AS (SELECT mint FROM creates_t UNION SELECT mint FROM bc)
        SELECT a.mint, c.create_ms, c.create_slot, c.create_event_index, (c.mint IS NOT NULL) AS has_create,
               (m.mint IS NOT NULL) AS reached_complete, m.complete_ms, m.complete_slot, m.complete_event_index,
               b.bc_first_ms, b.bc_last_ms, b.bc_buy_sol, b.bc_sell_sol, coalesce(b.bc_n_trades, 0)::BIGINT AS bc_n_trades,
               coalesce(b.bc_n_traders, 0)::BIGINT AS bc_n_traders
        FROM allm a LEFT JOIN creates_t c USING (mint) LEFT JOIN complete_t m USING (mint) LEFT JOIN bc b USING (mint)
        ORDER BY a.mint""").fetch_arrow_table()
    write(tokens, out / "tokens_d1.parquet")

    # bars_1m (bonding only; same columns as hunt-shared bars minus v_lamports)
    if have_stage:
        con.execute("CREATE TABLE pf AS SELECT mint, trader, (min(ms) // 60000) * 60000 AS first_min FROM stage GROUP BY 1, 2")
        for k in [r[0] for r in con.execute("SELECT DISTINCT ms // 86400000 FROM stage ORDER BY 1").fetchall()]:
            day = datetime.fromtimestamp(k * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
            con.execute(f"""CREATE OR REPLACE TEMP TABLE d AS
                SELECT *, row_number() OVER (ORDER BY ord, seq) AS rn FROM stage WHERE ms // 86400000 = {k}""")
            bars = con.execute("""
                WITH b AS (
                  SELECT mint, 'pump_bonding' AS venue, (ms // 60000) * 60000 AS minute_ms,
                    arg_min(price, rn) AS open, max(price) AS high, min(price) AS low, arg_max(price, rn) AS close,
                    (sum(lam) / nullif(sum(tok), 0)) / 1000.0 AS vwap,
                    sum(lam) FILTER (WHERE is_buy) / 1e9 AS buy_sol, sum(lam) FILTER (WHERE NOT is_buy) / 1e9 AS sell_sol,
                    count(*) FILTER (WHERE is_buy)::INTEGER AS n_buys, count(*) FILTER (WHERE NOT is_buy)::INTEGER AS n_sells,
                    count(DISTINCT trader) FILTER (WHERE is_buy)::INTEGER AS n_distinct_buyers,
                    count(DISTINCT trader) FILTER (WHERE NOT is_buy)::INTEGER AS n_distinct_sellers,
                    (coalesce(sum(lam) FILTER (WHERE is_buy), 0) - coalesce(sum(lam) FILTER (WHERE NOT is_buy), 0)) / 1e9 AS net_sol,
                    max(lam) FILTER (WHERE is_buy) / 1e9 AS max_buy_sol, max(lam) FILTER (WHERE NOT is_buy) / 1e9 AS max_sell_sol,
                    arg_max(q, rn) AS last_quote_reserve, arg_max(b, rn) AS last_base_reserve, max(slot) AS last_slot
                  FROM d GROUP BY mint, ms // 60000),
                n AS (
                  SELECT d.mint, (d.ms // 60000) * 60000 AS minute_ms, sum(d.lam) / 1e9 AS new_buy_sol,
                         count(DISTINCT d.trader)::INTEGER AS n_new_buyers
                  FROM d JOIN pf ON pf.mint = d.mint AND pf.trader = d.trader AND pf.first_min = (d.ms // 60000) * 60000
                  WHERE d.is_buy GROUP BY 1, 2)
                SELECT b.*, coalesce(n.new_buy_sol, 0) AS new_buy_sol, coalesce(n.n_new_buyers, 0)::INTEGER AS n_new_buyers,
                       coalesce(n.new_buy_sol, 0) / nullif(b.buy_sol, 0) AS new_buyer_share
                FROM b LEFT JOIN n ON n.mint = b.mint AND n.minute_ms = b.minute_ms
                ORDER BY b.mint, b.minute_ms""").fetch_arrow_table()
            write(bars, out / "bars_1m" / f"{day}.parquet")

    # wallet_mint_d1 (bonding only; no fee or pnl column: fees are not in the D1 mask)
    wm = con.execute("""
        SELECT trader, mint, min(ms) AS first_ms, max(ms) AS last_ms,
          coalesce(sum(lam) FILTER (WHERE is_buy), 0) / 1e9 AS buy_sol, coalesce(sum(lam) FILTER (WHERE NOT is_buy), 0) / 1e9 AS sell_sol,
          count(*) FILTER (WHERE is_buy)::INTEGER AS n_buys, count(*) FILTER (WHERE NOT is_buy)::INTEGER AS n_sells,
          coalesce(sum(tok) FILTER (WHERE is_buy), 0) AS buy_tok, coalesce(sum(tok) FILTER (WHERE NOT is_buy), 0) AS sell_tok,
          min(ms) FILTER (WHERE is_buy) AS first_buy_ms, arg_min(price, ord) FILTER (WHERE is_buy) AS first_buy_price
        FROM stage GROUP BY trader, mint ORDER BY trader, mint""").fetch_arrow_table()
    write(wm, out / "wallet_mint_d1.parquet")

    n_mig_no_complete = 0
    if have_stage and mig_filled:
        con.register("mig_t", pa.table({"mint": list(mig_filled)}))
        n_mig_no_complete = con.execute(
            "SELECT count(DISTINCT s.mint) FROM stage s JOIN mig_t USING (mint) "
            "WHERE s.mint NOT IN (SELECT mint FROM complete_t)").fetchone()[0]
    con.close()
    shutil.rmtree(out / "tmp", ignore_errors=True)

    manifest = {
        "git_head": _git_head(), "args": {"dataset": dataset, "start": start, "end_exclusive": end},
        "role": ROLE, "reader": reader, "resolved_gates": gates,
        "python": sys.version.split()[0], "pyarrow": pa.__version__, "duckdb": duckdb.__version__,
        "complete_rule": {"buy_with_base_reserve_at_or_below": COMPLETE_VIRTUAL_TOKEN_RESERVE_RAW},
        "complete_detection": {"mints_with_complete": len(complete_info), "exact_constant": det["exact"],
                               "strictly_below_constant": det["below"],
                               "qa_traded_mints_with_migrate_pos_but_no_detected_complete": n_mig_no_complete},
        "skipped_rows": skipped, "skipped_bad_reserves": n_bad_reserves, "create_rows_not_type_create": n_create_other,
        "n_migrate_positions": len(mig_filled), "inputs": inputs, "row_counts": {k: v["rows"] for k, v in outputs.items()},
        "outputs": outputs, "coverage_per_hour": coverage,
    }
    mp = out / "MANIFEST.json"
    mp.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return manifest


def summary(m: Mapping[str, Any]) -> str:
    """Counts and hashes only; never a row."""
    lines = [f"git_head {m['git_head']}", f"window {m['args']}", f"reader {m['reader']}",
             f"complete_detection {json.dumps(m['complete_detection'], sort_keys=True)}",
             f"skipped_rows {json.dumps(m['skipped_rows'], sort_keys=True)}",
             f"n_inputs {len(m['inputs'])} n_migrate_positions {m['n_migrate_positions']}"]
    lines += [f"output {k} rows={v['rows']} sha256={v['sha256']}" for k, v in sorted(m["outputs"].items())]
    lines += [f"input {i['kind']} {i['hour']} sha256={i['sha256']}" for i in m["inputs"]]
    lines += [f"coverage {c['hour']} prints={c['bonding_prints']} mints={c['mints_with_prints']} "
              f"creates={c['creates']} completes={c['completes']}" for c in m["coverage_per_hour"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", required=True, choices=sorted(oc.DATASET_START))
    ap.add_argument("--start", required=True, help="YYYY-MM-DDTHH")
    ap.add_argument("--end", required=True, help="YYYY-MM-DDTHH, exclusive")
    ap.add_argument("--out", required=True)
    ap.add_argument("--memory-gb", type=int, default=3)
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    try:
        m = run(a)
    except (LayerRefused, oc.OctoberRefused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(summary(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
