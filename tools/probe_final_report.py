"""DEC-019 section 7 closing report from the probe fills ledger (read-only).

Reads `probe-fills*.jsonl` (mode == "live" rows only), prints JSON and markdown tables: per-build
P&L, fee split and projection, entry and exit latency, skips, hour-of-day. stdlib only. It never
reads a key, the paper runner's files, or the tape. Execution measurement, not edge evidence.

    python3 -I tools/probe_final_report.py --fills FILE [--out-json F] [--out-md F] [--build START_MS:SHA ...]

Builds are never pooled for P&L: one row per build, then an "all (pooled, not a result)" row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

LAMPORTS = 1_000_000_000
STAKE = 50_000_000  # 0.05 SOL
SLOT_MS = 400
# Same boundaries as tools/probe_sim_calibration.BUILDS (a trade belongs to the last boundary at or before its buy ts).
BUILDS: tuple[tuple[int, str], ...] = (
    (0, "8a6849b"),
    (1791223983000, "a25eb17"),
    (1791232766000, "7004b16"),
    (1791241796000, "faa3192"),
)
PROJECT_STAKES_SOL = (0.05, 0.25, 0.5)
ALL = "all (pooled, not a result)"


def build_of(ts_ms: int, builds: Iterable[tuple[int, str]] = BUILDS) -> str:
    name = None
    for start, sha in sorted(builds):
        if ts_ms >= start:
            name = sha
    return name or sorted(builds)[0][1]


def parse_builds(specs: list[str]) -> tuple[tuple[int, str], ...]:
    out = []
    for sp in specs:
        a, _, b = sp.partition(":")
        if not a.isdigit() or not b:
            raise ValueError(f"--build wants START_MS:SHA, got {sp!r}")
        out.append((int(a), b))
    return tuple(out)


def pct(vals: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100)."""
    if not vals:
        return None
    s = sorted(vals)
    i = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
    return s[i]


def dist(vals: list[float]) -> dict[str, Any]:
    v = [x for x in vals if x is not None]
    return {"n": len(v), "min": min(v) if v else None, "p50": pct(v, 50), "p90": pct(v, 90), "max": max(v) if v else None}


def load(path: str) -> tuple[list[dict[str, Any]], str]:
    h = hashlib.sha256()
    rows = []
    with open(path, "rb") as f:
        for line in f:
            h.update(line)
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows, h.hexdigest()


def pair_trips(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(round trips, failed buys, failed sell attempts). A trip is a landed buy matched in order, per mint, to the
    next landed sell with a pnl. Failed rows (landed false) are returned separately."""
    live = sorted((r for r in rows if r.get("mode") == "live"), key=lambda r: r["ts_ms"])
    open_buys: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trips, fbuys, fsells = [], [], []
    for r in live:
        if r["kind"] == "buy":
            if r.get("landed") and not r.get("err"):
                open_buys[r["mint"]].append(r)
            else:
                fbuys.append(r)
        elif r["kind"] == "sell":
            if r.get("landed") and r.get("pnl_lamports") is not None and open_buys[r["mint"]]:
                trips.append({"buy": open_buys[r["mint"]].pop(0), "sell": r})
            else:
                fsells.append(r)
    return trips, fbuys, fsells


def _stats(vals: list[float]) -> dict[str, Any]:
    return {"mean": statistics.fmean(vals) if vals else None, "median": statistics.median(vals) if vals else None}


def per_build(trips: list[dict[str, Any]], fbuys: list[dict[str, Any]], builds) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trips:
        groups[build_of(t["buy"]["ts_ms"], builds)].append(t)
    out: dict[str, Any] = {}
    names = [sha for _, sha in sorted(builds)] + [ALL]
    for name in names:
        g = trips if name == ALL else groups.get(name, [])
        pnl = [t["sell"]["pnl_lamports"] for t in g]
        reasons = Counter(t["sell"]["exit_reason"] for t in g)
        fb = [r for r in fbuys if name == ALL or build_of(r["ts_ms"], builds) == name]
        out[name] = {
            "n_round_trips": len(g), "tp": reasons.get("tp", 0), "sl": reasons.get("sl", 0),
            "time_stop": reasons.get("time_stop", 0),
            "other_exits": sum(v for k, v in reasons.items() if k not in ("tp", "sl", "time_stop")),
            "realized_lamports": sum(pnl), "mean_lamports": _stats(pnl)["mean"], "median_lamports": _stats(pnl)["median"],
            "mean_pct_of_stake": (statistics.fmean(pnl) / STAKE * 100) if pnl else None,
            "failed_buys": len(fb), "failed_buy_cost_lamports": sum(r.get("cost_lamports") or 0 for r in fb),
            "realized_incl_failed_buys_lamports": sum(pnl) - sum(r.get("cost_lamports") or 0 for r in fb),
        }
    return out


def fee_split(trips: list[dict[str, Any]], fbuys: list[dict[str, Any]], fsells: list[dict[str, Any]], builds) -> dict[str, Any]:
    """Per-trade means. The sell-leg pool fee is an ESTIMATE: the buy's pool_fee_est/spend rate applied to the sell's
    gross SOL out (the ledger logs no sell-side pool fee). Failed-attempt costs: failed sell rows' tx fees (also carried
    on the retried sell as failed_attempt_cost_lamports, so counted once from there) plus failed buys' tx fees."""
    out: dict[str, Any] = {}
    names = [sha for _, sha in sorted(builds)] + [ALL]
    for name in names:
        g = [t for t in trips if name == ALL or build_of(t["buy"]["ts_ms"], builds) == name]
        if not g:
            out[name] = {"n": 0}
            continue
        base = [t["buy"].get("base_fee_lamports", 0) + t["sell"].get("base_fee_lamports", 0) for t in g]
        prio = [t["buy"].get("priority_fee_lamports", 0) + t["sell"].get("priority_fee_lamports", 0) for t in g]
        pool_b = [t["buy"].get("pool_fee_est_lamports") or 0 for t in g]
        pool_s = []
        for t in g:
            b, s = t["buy"], t["sell"]
            rate = (b.get("pool_fee_est_lamports") or 0) / b["spend_lamports"] if b.get("spend_lamports") else 0
            pool_s.append(rate * (s.get("sol_received_lamports") or 0))
        rent_c = [t["buy"].get("rent_charged_lamports") or 0 for t in g]
        rent_r = [t["sell"].get("rent_refunded_lamports") or 0 for t in g]
        fail = [t["sell"].get("failed_attempt_cost_lamports") or 0 for t in g]
        fb = [r for r in fbuys if name == ALL or build_of(r["ts_ms"], builds) == name]
        fb_cost = sum(r.get("cost_lamports") or 0 for r in fb)
        n = len(g)
        m = statistics.fmean
        fixed = m(base) + m(prio) + (sum(fail) + fb_cost) / n
        pool = m(pool_b) + m(pool_s)
        d = {
            "n": n, "base_fee": m(base), "priority_fee": m(prio), "pool_fee_buy_est": m(pool_b), "pool_fee_sell_est": m(pool_s),
            "rent_charged": m(rent_c), "rent_refunded": m(rent_r), "rent_net_unrefunded": m(rent_c) - m(rent_r),
            "failed_attempt_cost": (sum(fail) + fb_cost) / n,
            "fixed_cost": fixed, "pool_cost_est": pool,
            "round_trip_cost": fixed + pool + (m(rent_c) - m(rent_r)),
        }
        d["pct_of_stake"] = {k: d[k] / STAKE * 100 for k in
                             ("base_fee", "priority_fee", "pool_fee_buy_est", "pool_fee_sell_est", "rent_net_unrefunded",
                              "failed_attempt_cost", "fixed_cost", "pool_cost_est", "round_trip_cost")}
        d["projection_pct_of_stake"] = {
            f"{s}": ((d["fixed_cost"] + d["rent_net_unrefunded"]) * (0.05 / s) + d["pool_cost_est"]) / STAKE * 100
            for s in PROJECT_STAKES_SOL}
        out[name] = d
    return out


def entry_latency(trips: list[dict[str, Any]], fbuys: list[dict[str, Any]], builds) -> dict[str, Any]:
    """decision -> send (ms_decision_to_send), send -> confirm (ms_send_to_confirm), decision -> landed
    (confirm_seen_ms - decision_t_ms; confirm_seen is when the executor saw the landing). Slots: landed_slot - state_slot
    (slots_between, exact). pool_slot / state_slot / snapshot_slot are the executor's pool-state READ slot, NOT the
    migration slot, so k against the migration is not in the ledger."""
    out: dict[str, Any] = {}
    names = [sha for _, sha in sorted(builds)] + [ALL]
    for name in names:
        bs = [t["buy"] for t in trips if name == ALL or build_of(t["buy"]["ts_ms"], builds) == name]
        out[name] = {
            "n": len(bs),
            "decision_to_send_ms": dist([b.get("ms_decision_to_send") for b in bs]),
            "send_to_confirm_ms": dist([b.get("ms_send_to_confirm") for b in bs]),
            "decision_to_landed_ms": dist([b["confirm_seen_ms"] - b["decision_t_ms"] for b in bs if b.get("confirm_seen_ms") and b.get("decision_t_ms")]),
            "state_read_to_landed_slots": dist([b.get("slots_between") for b in bs]),
            "landed_slot_minus_snapshot_slot": dist([b["landed_slot"] - b["snapshot_slot"] for b in bs if b.get("snapshot_slot")]),
        }
    return out


def exit_latency(trips: list[dict[str, Any]], builds) -> dict[str, Any]:
    """Exit lag in SLOTS = sell landed_slot - snapshot_slot, where snapshot_slot is the pool snapshot the exit rule fired
    on (on every sell row). first_exit_snap_slot is the FIRST post-landing snapshot (when the position started to be
    watched), not the trigger, so it is not used for lag."""
    out: dict[str, Any] = {}
    names = [sha for _, sha in sorted(builds)] + [ALL]
    for name in names:
        g = [t for t in trips if name == ALL or build_of(t["buy"]["ts_ms"], builds) == name]
        sells = [t["sell"] for t in g]
        sl = [s for s in sells if s["exit_reason"] == "sl"]
        tp = [s for s in sells if s["exit_reason"] == "tp"]
        lag = lambda ss: [s["landed_slot"] - s["snapshot_slot"] for s in ss if s.get("snapshot_slot")]
        gap_rets = [s["ret_exit_vs_entry_actual"] / 10000 for s in sl if s.get("ret_exit_vs_entry_actual") is not None]
        out[name] = {
            "n": len(sells),
            "exit_lag_slots_all": dist(lag(sells)), "exit_lag_slots_sl": dist(lag(sl)), "exit_lag_slots_tp": dist(lag(tp)),
            "decision_to_send_ms": dist([s.get("ms_decision_to_send") for s in sells]),
            "send_to_confirm_ms": dist([s.get("ms_send_to_confirm") for s in sells]),
            "exit_vs_quote_bps_sl": {**dist([s.get("exit_vs_quote_bps") for s in sl]),
                                      "mean": statistics.fmean([s["exit_vs_quote_bps"] for s in sl if s.get("exit_vs_quote_bps") is not None] or [0])
                                      if sl else None},
            "exit_vs_quote_bps_all": dist([s.get("exit_vs_quote_bps") for s in sells]),
            "sl_trigger_ret": dist([s["ret"] for s in sl]),
            "sl_realized_ret_vs_entry": dist([r for r in gap_rets]),
            "sl_n": len(sl),
            "sl_filled_worse_than_minus_40pct": sum(1 for r in gap_rets if r < -0.40),
            "sl_filled_worse_than_minus_50pct": sum(1 for r in gap_rets if r < -0.50),
            "sl_trigger_worse_than_minus_40pct": sum(1 for s in sl if s["ret"] < -0.40),
        }
    return out


def skips(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(r.get("reason") for r in rows if r.get("mode") == "live" and r.get("kind") == "skip"))


def hour_of_day(trips: list[dict[str, Any]]) -> dict[str, Any]:
    h: dict[int, list[int]] = defaultdict(list)
    for t in trips:
        h[datetime.fromtimestamp(t["buy"]["ts_ms"] / 1000, tz=timezone.utc).hour].append(t["sell"]["pnl_lamports"])
    return {f"{k:02d}": {"n": len(v), "pnl_lamports": sum(v), "mean_lamports": statistics.fmean(v)} for k, v in sorted(h.items())}


def build_report(rows: list[dict[str, Any]], builds=BUILDS, sha256: str | None = None) -> dict[str, Any]:
    trips, fbuys, fsells = pair_trips(rows)
    live = [r for r in rows if r.get("mode") == "live"]
    return {
        "source_sha256": sha256, "live_rows": Counter(r["kind"] for r in live), "n_round_trips": len(trips),
        "failed_buys": len(fbuys), "failed_sell_attempts": len(fsells),
        "builds": [list(b) for b in sorted(builds)],
        "per_build": per_build(trips, fbuys, builds), "fee_split": fee_split(trips, fbuys, fsells, builds),
        "entry_latency": entry_latency(trips, fbuys, builds), "exit_latency": exit_latency(trips, builds),
        "skips": skips(rows), "hour_of_day": hour_of_day(trips),
    }


def _f(x: Any, nd: int = 1) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    return f"{x:,}" if isinstance(x, int) else str(x)


def markdown(rep: dict[str, Any]) -> str:
    L: list[str] = []
    L += ["### Per build (round trips; builds never pooled, last row is arithmetic only)", "",
          "| build | n | tp | sl | time_stop | realized lamports | mean | median | mean % of 0.05 SOL | failed buys | realized incl. failed buys |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for k, v in rep["per_build"].items():
        L.append(f"| {k} | {v['n_round_trips']} | {v['tp']} | {v['sl']} | {v['time_stop']} | {_f(v['realized_lamports'])} | "
                 f"{_f(v['mean_lamports'], 0)} | {_f(v['median_lamports'], 0)} | {_f(v['mean_pct_of_stake'], 2)} | {v['failed_buys']} | {_f(v['realized_incl_failed_buys_lamports'])} |")
    L += ["", "### Fee split per round trip (mean lamports; sell-leg pool fee is an estimate)", "",
          "| build | n | base | priority | pool buy est | pool sell est | rent charged | rent refunded | rent net | failed attempts | RT cost |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for k, v in rep["fee_split"].items():
        if not v["n"]:
            continue
        L.append(f"| {k} | {v['n']} | {_f(v['base_fee'], 0)} | {_f(v['priority_fee'], 0)} | {_f(v['pool_fee_buy_est'], 0)} | {_f(v['pool_fee_sell_est'], 0)} | "
                 f"{_f(v['rent_charged'], 0)} | {_f(v['rent_refunded'], 0)} | {_f(v['rent_net_unrefunded'], 0)} | {_f(v['failed_attempt_cost'], 0)} | {_f(v['round_trip_cost'], 0)} |")
    L += ["", "### Round-trip cost as % of stake, and projection (fixed parts scale down with stake, pool fee % does not)", "",
          "| build | base % | priority % | pool est % | failed % | rent net % | RT cost % at 0.05 | at 0.25 | at 0.5 |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for k, v in rep["fee_split"].items():
        if not v["n"]:
            continue
        p, pr = v["pct_of_stake"], v["projection_pct_of_stake"]
        L.append(f"| {k} | {_f(p['base_fee'], 2)} | {_f(p['priority_fee'], 2)} | {_f(p['pool_cost_est'], 2)} | {_f(p['failed_attempt_cost'], 2)} | "
                 f"{_f(p['rent_net_unrefunded'], 2)} | {_f(pr['0.05'], 2)} | {_f(pr['0.25'], 2)} | {_f(pr['0.5'], 2)} |")
    L += ["", "### Entry latency (ms; slots = landed_slot − pool-state read slot)", "",
          "| build | n | dec→send p50/p90/max | send→confirm p50/p90/max | dec→landed p50/p90/max | read→landed slots p50/p90/max |",
          "| --- | ---: | --- | --- | --- | --- |"]
    tri = lambda d: f"{_f(d['p50'], 0)} / {_f(d['p90'], 0)} / {_f(d['max'], 0)}"
    for k, v in rep["entry_latency"].items():
        L.append(f"| {k} | {v['n']} | {tri(v['decision_to_send_ms'])} | {tri(v['send_to_confirm_ms'])} | {tri(v['decision_to_landed_ms'])} | {tri(v['state_read_to_landed_slots'])} |")
    L += ["", "### Exit lag in slots (sell landed_slot − the snapshot slot the exit fired on)", "",
          "| build | n | all p50/p90/max | sl p50/p90/max | tp p50/p90/max | dec→send ms | send→confirm ms |",
          "| --- | ---: | --- | --- | --- | --- | --- |"]
    for k, v in rep["exit_latency"].items():
        L.append(f"| {k} | {v['n']} | {tri(v['exit_lag_slots_all'])} | {tri(v['exit_lag_slots_sl'])} | {tri(v['exit_lag_slots_tp'])} | "
                 f"{tri(v['decision_to_send_ms'])} | {tri(v['send_to_confirm_ms'])} |")
    L += ["", "### Stop-loss exits (gap-through)", "",
          "| build | sl n | exit vs quote bps p50/p90/max | trigger ret p50 / min | realized ret vs entry p50 / min | trigger < −40% | filled < −40% | filled < −50% |",
          "| --- | ---: | --- | --- | --- | ---: | ---: | ---: |"]
    for k, v in rep["exit_latency"].items():
        q, tr, rr = v["exit_vs_quote_bps_sl"], v["sl_trigger_ret"], v["sl_realized_ret_vs_entry"]
        mn = lambda d, key: _f(d.get(key), 3)
        L.append(f"| {k} | {v['sl_n']} | {tri(q)} | {_f(tr['p50'], 3)} / {_f(min_of(tr), 3)} | {_f(rr['p50'], 3)} / {_f(min_of(rr), 3)} | "
                 f"{v['sl_trigger_worse_than_minus_40pct']} | {v['sl_filled_worse_than_minus_40pct']} | {v['sl_filled_worse_than_minus_50pct']} |")
    L += ["", "### Skips (live)", "", "| reason | n |", "| --- | ---: |"]
    L += [f"| {k} | {v} |" for k, v in rep["skips"].items()]
    L += ["", "### Hour of day (UTC hour of the buy; small n per bucket, a sampling picture only)", "",
          "| UTC hour | trips | P&L lamports | mean |", "| --- | ---: | ---: | ---: |"]
    L += [f"| {k} | {v['n']} | {_f(v['pnl_lamports'])} | {_f(v['mean_lamports'], 0)} |" for k, v in rep["hour_of_day"].items()]
    return "\n".join(L) + "\n"


def min_of(d: dict[str, Any]) -> Any:
    return d.get("min")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fills", required=True)
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    ap.add_argument("--build", action="append", default=[], metavar="START_MS:SHA")
    a = ap.parse_args()
    rows, sha = load(a.fills)
    rep = build_report(rows, parse_builds(a.build) if a.build else BUILDS, sha)
    md = markdown(rep)
    if a.out_json:
        with open(a.out_json, "w") as f:
            json.dump(rep, f, indent=1, default=str)
    if a.out_md:
        with open(a.out_md, "w") as f:
            f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
