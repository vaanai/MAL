#!/usr/bin/env python3
"""Out-of-sample score for the frozen migrate-direct cell. Scoring only.

Does not import the forward runner, does not write a live book, and does not
read promotion or the risk ceilings. Parameters are the 2026-09-27T13:06:36Z freeze.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.latency_curve import (
    B_SLOT,
    B_SOL,
    EXITS,
    FLAT_FAIL,
    LANDS,
    MISS,
    ROUTES,
    SEND,
    SIZES,
    WINDOW_MS,
    WSOL,
    FailCurve,
    Pressure,
    _Mint,
    _anchor,
    _iter_trades,
    _pressure,
    _rss_mb,
    _slot_time,
    _state_index,
    _tpsl,
    _trim_heap,
    _try_buy,
    mixed_net,
    sealed_hours,
    wait_for_lag,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL
from tools.paper_price_path import TapePrint, print_from_trade_row

FROZEN_AT = "2026-09-27T13:06:36Z"
SELECTION_START = "2026-09-22T10:00:00Z"
FORWARD_START = "2026-09-28T00:00:00Z"
PRIORITY_LAMPORTS = 500_000
PRESSURE_INTERCEPT = -1.4548727851312098
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 1
SCHEMA = "migrate_direct_oos_v1"
# slot+1 start, tp50_sl30, direct. Same indexes as tools.latency_curve.
LAND_I = 0
EXIT_I = 1
ROUTE_I = 0
PORTAL_PPM = 0


def _parse_time(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _utc_day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def _curve() -> FailCurve:
    """Scale-1 curve from the selection window. Not refit."""
    return FailCurve(PRESSURE_INTERCEPT, B_SLOT * 1.0, B_SOL * 1.0, 1.0)


def prepare_row(row: dict[str, Any], *, receive: str) -> dict[str, Any] | None:
    """Backfill uses block time when receive is missing. Forward does not invent one."""
    if receive == "block":
        if row.get("t_recv_ms") is None:
            block = row.get("block_time")
            if not isinstance(block, int):
                return None
            row = dict(row)
            row["t_recv_ms"] = block * 1000
        return row
    if not isinstance(row.get("t_recv_ms"), int) or isinstance(row.get("t_recv_ms"), bool):
        return None
    return row


def cell_rows(
    fills: Sequence[TapePrint],
    *,
    trigger_slot: int,
    trigger_block_ms: int,
    ref_price: float | None,
    tape_through_ms: int,
    size_indexes: Sequence[int] = (0, 1),
) -> list[tuple[int, int, int, int, int, int, int]]:
    """One migration. (size_i, status, pri_sides, net0, gross, buys, nearby).

    Same slot+1 start buy and tp50_sl30 close as evaluate_path. Other cells are not computed.
    """
    _land_i, name, bound, k = LANDS[LAND_I]
    if (name, bound, k) != ("slot+1", "start", 1) or EXITS[EXIT_I] != "tp50_sl30" or ROUTES[ROUTE_I][0] != "direct":
        raise RuntimeError("frozen cell indexes drifted")
    target = trigger_slot + k
    idx = _state_index(fills, target, bound)
    fallback = fills[idx].t_recv_ms if idx >= 0 else trigger_block_ms
    landing_ms = _slot_time(fills, target, fallback)
    state = fills[idx] if idx >= 0 else None
    buys, nearby = (0, 0)
    if state is not None:
        buys, nearby = _pressure(fills, idx, state.slot, landing_ms)
    out: list[tuple[int, int, int, int, int, int, int]] = []
    for size_i in size_indexes:
        size = SIZES[size_i]
        buy = None if state is None else _try_buy(state, size, PORTAL_PPM, ref_price)
        if buy is None:
            out.append((size_i, MISS, 1, 0, 0, buys, nearby))
            continue
        assert state is not None
        result = _tpsl(
            fills,
            idx,
            buy,
            state.venue,
            size,
            PORTAL_PPM,
            landing_ms,
            tape_through_ms,
            k,
            bound,
            bound,
        )
        if result is None:
            continue
        net0, gross, sides, status = result
        out.append((size_i, status, sides, net0, gross, buys, nearby))
    return out


def _pct(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round(p * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def _ci_lo(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means = []
    for _ in range(BOOTSTRAP_DRAWS):
        total = 0.0
        for _i in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return _pct(means, 0.05)


def _ex_top(values: Sequence[float], k: int = 3) -> float | None:
    if len(values) <= k:
        return None
    ordered = sorted(values, reverse=True)
    return sum(values) - sum(ordered[:k])


def _load_creates(hours: Sequence[dict[str, Any]]) -> tuple[dict[str, _Mint], dict[str, str]]:
    found: dict[str, _Mint] = {}
    days: dict[str, str] = {}
    for hour in hours:
        path = hour.get("create")
        if path is None:
            continue
        day = str(hour["day"])
        for line in _iter_trades(path):
            if line.get("type") != "create":
                continue
            mint = line.get("mint")
            slot = line.get("slot")
            block = line.get("block_time")
            if not isinstance(mint, str) or not isinstance(slot, int) or not isinstance(block, int):
                continue
            quote_mint = line.get("quote_mint")
            if isinstance(quote_mint, str) and quote_mint and quote_mint != WSOL:
                continue
            block_ms = block * 1000
            prev = found.get(mint)
            if prev is not None and prev.block_ms <= block_ms:
                continue
            sig = line.get("signature") if isinstance(line.get("signature"), str) else None
            found[mint] = _Mint(slot, block_ms, 0, _anchor(line, slot, block_ms, sig))
            days[mint] = day
    return found, days


def _ref_migrate(fills: Sequence[TapePrint], mig_slot: int) -> float | None:
    for pr in fills:
        if pr.slot == mig_slot and pr.price_sol > 0:
            return pr.price_sol
    return None


def stream_cell(
    hours: Sequence[dict[str, Any]],
    *,
    receive: str,
    lag_path: Path | None,
    size_indexes: Sequence[int],
    creates: dict[str, _Mint] | None = None,
    day_of: dict[str, str] | None = None,
    admit_on_bond: bool = False,
    min_mig_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Stream sealed hours in order. Only the frozen cell is scored."""
    hot: dict[str, _Mint] = dict(creates or {})
    watch: dict[str, _Mint] = {}
    days: dict[str, str] = dict(day_of or {})
    out: list[dict[str, Any]] = []
    if lag_path is not None:
        wait_for_lag(lag_path)
    last_lag = time.time()
    lines = 0
    mig_rows = 0

    def pause_if_due(label: str) -> None:
        nonlocal last_lag
        if lag_path is None or time.time() - last_lag < 600:
            return
        lag = wait_for_lag(lag_path)
        last_lag = time.time()
        print(f"lag_ms={lag} {label}", file=sys.stderr, flush=True)

    def score_mig(mint_id: str, mint: _Mint, through_ms: int) -> None:
        nonlocal mig_rows
        if mint.mig_slot is None or mint.mig_done:
            return
        if min_mig_ms is not None and (mint.mig_ms or 0) < min_mig_ms:
            mint.mig_done = True
            return
        fills = mint.fillable(migrate=True)
        assert mint.mig_slot is not None and mint.mig_ms is not None
        ref = _ref_migrate(fills, mint.mig_slot)
        produced = cell_rows(
            fills,
            trigger_slot=mint.mig_slot,
            trigger_block_ms=mint.mig_ms,
            ref_price=ref,
            tape_through_ms=through_ms,
            size_indexes=size_indexes,
        )
        day = days.get(mint_id) or _utc_day(mint.mig_ms)
        for size_i, status, sides, net0, gross, buys, nearby in produced:
            out.append(
                {
                    "day": day,
                    "size_i": size_i,
                    "status": status,
                    "sides": sides,
                    "net0": net0,
                    "gross": gross,
                    "buys": buys,
                    "nearby": nearby,
                }
            )
        mig_rows += 1
        mint.mig_done = True

    def flush(now_ms: int, final: bool) -> None:
        for mint_id, mint in list(hot.items()):
            if not mint.create_done and (final or now_ms >= mint.block_ms + WINDOW_MS):
                mint.create_done = True
                if mint.mig_slot is None:
                    mint.release()
                    watch[mint_id] = mint
                    hot.pop(mint_id, None)
                elif final or now_ms >= (mint.mig_ms or 0) + WINDOW_MS:
                    through = now_ms if now_ms >= (mint.mig_ms or 0) + WINDOW_MS else (mint.mig_ms or 0)
                    score_mig(mint_id, mint, through)
                    hot.pop(mint_id, None)
                else:
                    mint.drop_before(mint.mig_slot)
            elif mint.create_done and mint.mig_slot is not None and not mint.mig_done:
                if final or now_ms >= (mint.mig_ms or 0) + WINDOW_MS:
                    through = now_ms if now_ms >= (mint.mig_ms or 0) + WINDOW_MS else (mint.mig_ms or 0)
                    score_mig(mint_id, mint, through)
                    hot.pop(mint_id, None)
        if final:
            for mint_id, mint in list(watch.items()):
                if mint.mig_slot is not None and not mint.mig_done:
                    score_mig(mint_id, mint, mint.mig_ms or 0)

    now_ms = 0
    for hour in hours:
        pause_if_due(f"hour={hour['hour']}")
        print(
            f"hour={hour['hour']} hot={len(hot)} watch={len(watch)} mig={mig_rows} rss_mb={_rss_mb()}",
            file=sys.stderr,
            flush=True,
        )
        for raw in _iter_trades(hour["trade"]):
            lines += 1
            if lines % 2_000_000 == 0:
                pause_if_due(f"lines={lines}")
                print(f"lines={lines} mig={mig_rows} rss_mb={_rss_mb()}", file=sys.stderr, flush=True)
            row = prepare_row(raw, receive=receive)
            if row is None:
                continue
            mint_id = row.get("mint")
            if not isinstance(mint_id, str):
                continue
            parsed = print_from_trade_row(row)
            if parsed is None:
                continue
            _name, pr = parsed
            now_ms = pr.t_recv_ms if pr.t_recv_ms > now_ms else now_ms
            mint = hot.get(mint_id)
            watching = False
            if mint is None:
                mint = watch.get(mint_id)
                watching = mint is not None
            if mint is None and admit_on_bond and pr.venue == "pump_bonding":
                mint = _Mint(pr.slot, pr.t_recv_ms, 0, None)
                hot[mint_id] = mint
                days.setdefault(mint_id, _utc_day(pr.t_recv_ms))
            if mint is None:
                continue
            if watching:
                if pr.venue != "pumpswap" or not mint.had_bond or mint.mig_done:
                    continue
                mint.add(pr)
                watch.pop(mint_id, None)
                hot[mint_id] = mint
            else:
                mint.add(pr)
            if lines % 200_000 == 0:
                flush(now_ms, False)
        flush(max(now_ms, int(hour.get("end") or 0) * 1000), False)
        _trim_heap()
    flush(now_ms, True)
    print(f"done lines={lines} mig={mig_rows} rows={len(out)}", file=sys.stderr, flush=True)
    return out


def summarize(rows: Sequence[dict[str, Any]], *, size_i: int) -> dict[str, Any]:
    picked = [row for row in rows if int(row["size_i"]) == size_i]
    size = SIZES[size_i]
    curve = _curve()
    flat_vals: list[float] = []
    press_vals: list[float] = []
    days: dict[str, float] = {}
    press_days: dict[str, float] = {}
    gross = 0
    sends = 0
    for row in picked:
        status = int(row["status"])
        sides = int(row["sides"])
        net0 = int(row["net0"])
        if status == SEND:
            sends += 1
            p_flat = FLAT_FAIL
            p_press = curve.p(Pressure(int(row["buys"]), int(row["nearby"])))
        else:
            p_flat = 0.0
            p_press = 0.0
        flat = mixed_net(net0, sides, status, PRIORITY_LAMPORTS, p_flat)
        pressed = mixed_net(net0, sides, status, PRIORITY_LAMPORTS, p_press)
        flat_vals.append(flat)
        press_vals.append(pressed)
        day = str(row["day"])
        days[day] = days.get(day, 0.0) + flat
        press_days[day] = press_days.get(day, 0.0) + pressed
        gross += int(row["gross"])
    n = len(picked)
    ex = _ex_top(flat_vals, 3)
    ex_p = _ex_top(press_vals, 3)
    n_days = len(days)
    days_positive = sum(1 for total in days.values() if total > 0)
    press_days_positive = sum(1 for total in press_days.values() if total > 0)
    mean_flat = 0.0 if n == 0 else sum(flat_vals) / n
    mean_press = 0.0 if n == 0 else sum(press_vals) / n
    ci_flat = 0.0 if n == 0 else _ci_lo(flat_vals)
    ci_press = 0.0 if n == 0 else _ci_lo(press_vals)

    def clears(ci: float, ex_sol: float | None, positive: int) -> bool:
        majority = n_days > 0 and positive * 2 > n_days
        return bool(
            n >= 100
            and n_days >= 5
            and majority
            and ci > 0
            and ex_sol is not None
            and ex_sol > 0
        )

    flat_ex = None if ex is None else ex / LAMPORTS_PER_SOL
    press_ex = None if ex_p is None else ex_p / LAMPORTS_PER_SOL
    return {
        "size_sol": size / LAMPORTS_PER_SOL,
        "n": n,
        "days": n_days,
        "days_positive": days_positive,
        "fill_rate": None if n == 0 else sends / n,
        "sends": sends,
        "mean_net_flat_pct": None if n == 0 else mean_flat / size * 100.0,
        "mean_net_flat_sol": None if n == 0 else mean_flat / LAMPORTS_PER_SOL,
        "ci_lo_flat_pct": None if n == 0 else ci_flat / size * 100.0,
        "ci_lo_flat_sol": None if n == 0 else ci_flat / LAMPORTS_PER_SOL,
        "ex_top3_flat_sol": flat_ex,
        "mean_net_pressure_pct": None if n == 0 else mean_press / size * 100.0,
        "mean_net_pressure_sol": None if n == 0 else mean_press / LAMPORTS_PER_SOL,
        "ci_lo_pressure_pct": None if n == 0 else ci_press / size * 100.0,
        "ci_lo_pressure_sol": None if n == 0 else ci_press / LAMPORTS_PER_SOL,
        "ex_top3_pressure_sol": press_ex,
        "pressure_days_positive": press_days_positive,
        "mean_gross_pct": None if n == 0 else (gross / n) / size * 100.0,
        "promotion_clear": clears(ci_flat, flat_ex, days_positive) and clears(ci_press, press_ex, press_days_positive),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": "migrate_direct_oos_manifest_v1", "frozen_at": FROZEN_AT, "hours": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"schema": "migrate_direct_oos_manifest_v1", "frozen_at": FROZEN_AT, "hours": []}
    data.setdefault("hours", [])
    data["frozen_at"] = FROZEN_AT
    return data


def oos_hours(backfill: Path) -> list[dict[str, Any]]:
    """Sealed hours that end at or before the selection window opens."""
    return sealed_hours(backfill, _parse_time(SELECTION_START))


def _write_rows(path: Path, rows: Iterable[dict[str, Any]], *, append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _report(rows: Sequence[dict[str, Any]], *, hours: Sequence[str], added: Sequence[str], kind: str) -> dict[str, Any]:
    sizes = (1, 0) if kind == "oos" else (1,)
    by_size = {("0.5" if size_i == 1 else "0.05"): summarize(rows, size_i=size_i) for size_i in sizes}
    return {
        "schema": SCHEMA,
        "frozen_at": FROZEN_AT,
        "kind": kind,
        "priority_lamports": PRIORITY_LAMPORTS,
        "tip_lamports": 0,
        "pressure_intercept": PRESSURE_INTERCEPT,
        "hours": list(hours),
        "hours_added": list(added),
        "sizes": by_size,
    }


def update_oos(backfill: Path, out_dir: Path, lag_path: Path | None) -> dict[str, Any]:
    """Score sealed pre-selection hours that are not in the manifest yet."""
    hours = oos_hours(backfill)
    manifest_path = out_dir / "manifest.json"
    attempts_path = out_dir / "attempts.jsonl"
    manifest = _manifest(manifest_path)
    done = {str(hour) for hour in manifest.get("hours") or []}
    new = [hour for hour in hours if hour["hour"] not in done]
    added = [hour["hour"] for hour in new]
    if new:
        oldest_new = min(hour["hour"] for hour in new)
        rebuild = (not done) or max(hour["hour"] for hour in new) >= min(done)
        if rebuild:
            creates, day_of = _load_creates(hours)
            rows = stream_cell(
                hours,
                receive="block",
                lag_path=lag_path,
                size_indexes=(0, 1),
                creates=creates,
                day_of=day_of,
            )
            _write_rows(attempts_path, rows, append=False)
            kept = [hour["hour"] for hour in hours]
        else:
            creates, day_of = _load_creates(new)
            stream_from = [hour for hour in hours if hour["hour"] >= oldest_new]
            rows = stream_cell(
                stream_from,
                receive="block",
                lag_path=lag_path,
                size_indexes=(0, 1),
                creates=creates,
                day_of=day_of,
            )
            _write_rows(attempts_path, rows, append=True)
            kept = sorted(done | set(added))
        manifest = {"schema": "migrate_direct_oos_manifest_v1", "frozen_at": FROZEN_AT, "hours": kept}
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    else:
        kept = sorted(done)
    report = _report(_read_jsonl(attempts_path), hours=kept, added=added, kind="oos")
    _write_report(out_dir, report)
    return report


def update_forward(tape_dir: Path, out_dir: Path, lag_path: Path | None, now_s: int | None = None) -> dict[str, Any]:
    """Score sealed live-tape hours at or after the clean clock. Size 0.5 only."""
    start_s = _parse_time(FORWARD_START)
    now = int(time.time()) if now_s is None else int(now_s)
    hours = forward_hours(tape_dir, start_s, now)
    manifest_path = out_dir / "manifest.json"
    attempts_path = out_dir / "attempts.jsonl"
    manifest = _manifest(manifest_path)
    done = {str(hour) for hour in manifest.get("hours") or []}
    new = [hour for hour in hours if hour["hour"] not in done]
    added = [hour["hour"] for hour in new]
    if new:
        # Restream every sealed forward hour so a migration that crosses an hour
        # is scored once. The manifest still records which hours were added.
        rows = stream_cell(
            hours,
            receive="tape",
            lag_path=lag_path,
            size_indexes=(1,),
            admit_on_bond=True,
            min_mig_ms=start_s * 1000,
        )
        _write_rows(attempts_path, rows, append=False)
        kept = sorted(done | set(added))
        manifest = {"schema": "migrate_direct_oos_manifest_v1", "frozen_at": FROZEN_AT, "hours": kept}
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    else:
        kept = sorted(done)
    report = _report(_read_jsonl(attempts_path), hours=kept, added=added, kind="forward")
    _write_report(out_dir, report)
    return report


def forward_hours(tape_dir: Path, start_s: int, now_s: int) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not tape_dir.is_dir():
        return found
    seen: set[str] = set()
    for path in sorted(tape_dir.glob("trades-*")):
        name = path.name
        key = name[len("trades-") :].split(".jsonl")[0]
        if len(key) != 13 or key in seen:
            continue
        try:
            hour_s = int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
        if hour_s < start_s:
            continue
        sealed = name.endswith(".zst") or name.endswith(".gz") or (name.endswith(".jsonl") and now_s >= hour_s + 3600)
        if not sealed:
            continue
        seen.add(key)
        found.append({"hour": key, "day": key[:10], "end": hour_s + 3600, "trade": path, "create": None})
    found.sort(key=lambda hour: hour["hour"])
    return found


def _write_report(out_dir: Path, report: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frozen migrate-direct out-of-sample score")
    sub = parser.add_subparsers(dest="cmd", required=True)
    oos = sub.add_parser("oos", help="Append sealed backfill hours before the selection window")
    oos.add_argument("--backfill", type=Path, required=True)
    oos.add_argument("--out", type=Path, required=True)
    oos.add_argument("--lag-file", type=Path, default=Path("/var/lib/mal/paper/forward-paper/runner-status.json"))
    fwd = sub.add_parser("forward", help="Append sealed forward hours from the clean clock at 0.5 SOL")
    fwd.add_argument("--tape", type=Path, required=True)
    fwd.add_argument("--out", type=Path, required=True)
    fwd.add_argument("--lag-file", type=Path, default=Path("/var/lib/mal/paper/forward-paper/runner-status.json"))
    args = parser.parse_args(argv)
    if args.cmd == "oos":
        report = update_oos(args.backfill, args.out, args.lag_file)
    else:
        report = update_forward(args.tape, args.out, args.lag_file)
    sys.stdout.write(json.dumps(report["sizes"]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
