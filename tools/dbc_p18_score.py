"""P18 scorer (build PR2): scores the P18 forward paper book's SEALED live decisions under the CLAUDE.md promotion gate. Nothing is registered by this module.

P18 is a paper copy of D1 m10 (docs/research/p33-dbc-p18/STAGING.md, QP-RULING-1026-p33-p18.md). This module reads the hash-chained decision log written by
tools/dbc_p18_run.py (decision rows only: kind, key, block time; never an exit row, which is outcome-linked), finds the mechanical read boundary B, and prices the
decisions before B with the EXISTING frozen DBC pricer (tools/dbc_fwd_sec3.Pricer / price, tools/dbc_fwd_read.verdict). It does not edit any pinned file; the window
guard of tools/dbc_price.py is driven from here by a scoped context manager that restores the module globals.

Rules (all fixed before any P18 row exists):
  * Clock: the scorer refuses a clock earlier than 2026-11-01T00:10Z and refuses ANY decision row with a block time before the clock.
  * Boundary B: the first 00:00Z with >= 100 decisions (counted by decision timestamp only, outcome-blind) on >= 5 distinct UTC dates in [clock, B). The scorer
    refuses when B does not exist yet or `now` < B. Only decisions before B are scored.
  * Deciding legs: 1.9 s both legs, k = round(1.9 / slot_s) per hour. Strand (position open at curve completion) = FULL LOSS (stake + buy send + stranded rent).
    Entry-leg robustness at 2.5 s (exit 1.9 s) is DECIDING (flat AND pressure mean > 0).
  * Report-only, never deciding, never combined into a pass: fixed k = 9; 3.4 s entry; 3.4 s exit; immediate-DAMM-sell strand line (prints how many strands it
    covers); rent-charged is not repeated here.
  * Gate (both fail models, flat 15 % and pressure at slope scale 1): >= 100 trades (and >= 100 fills), >= 5 UTC days with a strict majority positive, CI90 lower bound of
    mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile), total SOL > 0 after removing the top 3 trades; plus R11's ex-best-day > 0 and mean > 0.
  * rows_after_drop (the pruning engine's counter) must be 0 or explained; unknown or unexplained refuses the read.
  * Decisions not found in the day backfill: > 1 % is NOT_DECIDABLE (AM2); one-look marker of its own.

DISCLOSURE (printed in every output): the m10 confirmation read's trades (window 2026-10-25 .. 2026-11-01T00:10Z) never count toward P18. P18's gate sample is the live
decisions from the P18 clock on. Winner's curse: m10 was the best of 23 screened DBC cells. No book has cleared the promotion gate.

    python3 -m tools.dbc_p18_score --log-root DIR --clock 2026-11-02T00:00:00Z --backfill-root DIR --status-json status.json --out OUTDIR
    python3 -m tools.dbc_p18_score --log-root DIR --clock ... --counts-only       # day-1 style dry score: decision kind + timestamp only, no pricing, no marker
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import contextlib
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from tools import arena_stack as AS
from tools import dbc_fwd as F
from tools import dbc_fwd_read as R
from tools import dbc_fwd_sec3 as X
from tools import dbc_price as L
from tools import dbc_screen as S
from tools.arena_declog import DecisionLog

SCHEMA = "dbc_p18_score_v1"
EARLIEST_CLOCK = datetime(2026, 11, 1, 0, 10, tzinfo=timezone.utc)  # a P18 clock before the m10 read can have finished is refused
MIN_TRADES = 100
MIN_DAYS = 5
FIXED_K = 9  # report-only line
MARKER = "/data/mal/hunt-1008/push-1016/hunt29/dbc/P18_READ_DONE_v1"  # own one-look marker (never the m10 one)
OUT_JSON, OUT_MD = "p18_score.json", "p18_score.md"
DISCLOSURE = ("The m10 confirmation read's trades (2026-10-25 .. 2026-11-01T00:10Z) NEVER count toward P18; P18's sample is the live decisions from its own clock. "
              "m10 is in-sample (best of 23 screened DBC cells). No book has cleared the promotion gate; this module's output is a paper read, not a licence.")


class ScoreRefused(Exception):
    """The scorer refused to read: nothing was priced and no marker was claimed."""


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)


# ------------------------------------------------------------------------------------------------------------------------------ decisions and boundary
def load_decisions(log_root: str, clock: datetime) -> dict[str, Any]:
    """Verify the hash chain, then take ONLY rows of type 'decision' (key, block time). Refuses a clock before EARLIEST_CLOCK and any decision before the clock.
    Exit, hb and other rows are not read for content (start rows are only counted)."""
    if clock < EARLIEST_CLOCK:
        raise ScoreRefused(f"clock {clock:%Y-%m-%dT%H:%M:%SZ} is before {EARLIEST_CLOCK:%Y-%m-%dT%H:%M:%SZ} (the m10 read cannot have finished)")
    log = DecisionLog(log_root)
    try:
        head, last_seq = log.verify(F.BOOK)
    except Exception as e:  # noqa: BLE001 - a broken chain is a refusal, whatever raised it
        raise ScoreRefused(f"decision log chain does not verify: {type(e).__name__}: {str(e)[:120]}") from e
    if last_seq < 0:
        raise ScoreRefused("decision log is empty")
    rows = log.rows(F.BOOK)
    ct = clock.timestamp()
    dec: list[dict[str, Any]] = []
    seen: set[str] = set()
    n_start = 0
    for r in rows:
        t = r.get("type")
        if t == "start":
            n_start += 1
        if t != "decision":
            continue
        bt = float(r["block_time"])
        if bt < ct:
            raise ScoreRefused(f"decision seq {r.get('seq')} (block time {utc(bt):%Y-%m-%dT%H:%M:%SZ}) is before the clock {clock:%Y-%m-%dT%H:%M:%SZ}")
        if r["key"] in seen:
            raise ScoreRefused(f"duplicate decision key {r['key']} (seq {r.get('seq')})")
        seen.add(r["key"])
        dec.append({"key": r["key"], "bt": bt, "seq": r["seq"]})
    return {"decisions": dec, "head": head, "last_seq": last_seq, "start_rows": n_start, "restarts": n_start - 1 if n_start else 0}


def boundary(bts: list[float], clock: datetime, min_n: int = MIN_TRADES, min_days: int = MIN_DAYS) -> dict[str, Any] | None:
    """Mechanical read boundary B: the first 00:00Z after the clock such that the decisions with a block time in [clock, B) number >= min_n on >= min_days distinct UTC
    dates. Outcome-blind: uses decision timestamps only. Returns None when no such 00:00Z exists in the data (the read is not due)."""
    if not bts:
        return None
    ts = sorted(bts)
    t0 = clock.timestamp()
    day = utc(t0).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    last = utc(ts[-1])
    while day <= last + timedelta(days=1):
        sel = [t for t in ts if t0 <= t < day.timestamp()]
        dates = {S.day_of(t) for t in sel}
        if len(sel) >= min_n and len(dates) >= min_days:
            return {"B": day, "B_utc": f"{day:%Y-%m-%dT%H:%M:%SZ}", "n": len(sel), "dates": sorted(dates)}
        day += timedelta(days=1)
    return None


def counts_only(log_root: str, clock: datetime) -> dict[str, Any]:
    """Dry-score counts (day 1): decision count per UTC date and the boundary status, from decision kind + timestamp only."""
    d = load_decisions(log_root, clock)
    by_day: dict[str, int] = {}
    for x in d["decisions"]:
        u = S.day_of(x["bt"])
        by_day[u] = by_day.get(u, 0) + 1
    b = boundary([x["bt"] for x in d["decisions"]], clock)
    return {"schema": SCHEMA + "_counts", "n_decisions": len(d["decisions"]), "by_day": dict(sorted(by_day.items())), "head": d["head"], "last_seq": d["last_seq"],
            "start_rows": d["start_rows"], "boundary": b["B_utc"] if b else None, "outcome_fields_read": False}


# ------------------------------------------------------------------------------------------------------------------------------ pricing helpers
@contextlib.contextmanager
def patched(mod: Any, name: str, value: Any) -> Iterator[None]:
    old = getattr(mod, name)
    setattr(mod, name, value)
    try:
        yield
    finally:
        setattr(mod, name, old)


@contextlib.contextmanager
def p18_window(root: str, start: datetime, end: datetime) -> Iterator[None]:
    """Drive dbc_price's forward-window guard for the P18 hours [start, end) under `root`, then restore its globals. Refuses anything touching the m10 window
    (before 2026-11-01T00Z) and anything that is not whole hours. The pinned file is not edited."""
    if start < L.FWD_END:
        raise ScoreRefused(f"window start {start:%Y-%m-%dT%H} overlaps the m10 forward window / earlier data (guard: >= {L.FWD_END:%Y-%m-%dT%H})")
    if end <= start or start.minute or start.second or end.minute or end.second:
        raise ScoreRefused("window must be whole hours with end > start")
    old = (L.FWD_ACTIVE, L.FWD_START, L.FWD_END)
    L.FWD_ACTIVE, L.FWD_START, L.FWD_END = root, start, end
    try:
        yield
    finally:
        L.FWD_ACTIVE, L.FWD_START, L.FWD_END = old


def models_from_pnl(pnl: Any, filled: Any, ssb: Any, nearby: Any, dates: list[str], send: float = float(L.SEND_LAMPORTS)) -> dict[str, Any]:
    """Both fail models on per-trade pnl (lamports): flat 15 % and pressure (slope scale 1, intercept refit to mean p 0.289), through the SAME arena_stack.fail_legs and
    dbc_screen.stat as the pricer. Returns stat dicts in SOL."""
    flat, press, c, p = AS.fail_legs(np.asarray(pnl, float), np.asarray(filled, bool), ssb, nearby, send=send)
    return {"flat": S.stat(flat / 1e9, dates), "press": S.stat(press / 1e9, dates), "press_intercept": c}


def make_price_fn(D: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    """price_fn(items, lat_in=1.9, lat_out=1.9, fixed_k=None) -> dbc_fwd_sec3.price output. lat_out / fixed_k are applied by scoped patches of the pinned modules'
    module globals (restored on exit); the deciding call uses neither."""
    pr = X.Pricer(D)

    def price_fn(items: list[tuple[str, float]], lat_in: float = L.LAT_S, lat_out: float | None = None, fixed_k: int | None = None) -> dict[str, Any]:
        with contextlib.ExitStack() as st:
            if lat_out is not None:
                st.enter_context(patched(L, "LAT_S", lat_out))
            if fixed_k is not None:
                st.enter_context(patched(S, "k_slots", lambda lat, ss, k=fixed_k: k))
            return X.price(pr.resolve_set(items, lat_in))

    return price_fn


def strand_line(strands: int, damm: list[dict[str, Any]] | None) -> dict[str, Any]:
    """REPORT-ONLY immediate-DAMM-sell line. `damm` = per covered strand {"key", "pnl_lamports"} (the immediate-sell pricing of strands whose pool migrated). Prints how many
    strands it covers; the delta is vs the deciding full loss. It never decides and is never combined into a pass."""
    cov = len(damm) if damm else 0
    out: dict[str, Any] = {"REPORT_ONLY": True, "strands_total": int(strands), "strands_covered": cov, "coverage": (cov / strands) if strands else None,
                           "unmodelled": ["fill price of the immediate sell", "competition from other holders selling in the first seconds"],
                           "note": "strands in pools that never migrate have no DAMM exit; they stay at full loss on this line too"}
    if damm:
        out["delta_vs_full_loss_sol_sum"] = float(sum(d["pnl_lamports"] - X.FULL_LOSS for d in damm) / 1e9)
        out["covered_pnl_lamports"] = [float(d["pnl_lamports"]) for d in damm]
    return out


def gate(p19: dict[str, Any], p25: dict[str, Any]) -> dict[str, Any]:
    """The promotion gate on the deciding line under both fail models: R11/R9 via dbc_fwd_read.verdict plus an explicit >= 100 trades per model."""
    v = R.verdict({"filled": p19.get("fills", 0), "flat": p19["flat"], "press": p19["press"]}, {"flat": p25["flat"], "press": p25["press"]})
    checks = v["checks"]
    ok = v["verdict"] == "PASS"
    for m in ("flat", "press"):
        n_ok = bool(p19[m].get("n", 0) >= MIN_TRADES)
        if isinstance(checks.get(m), dict):
            checks[m]["trades_ge_100"] = n_ok
        ok = ok and n_ok
    return {"verdict": "PASS" if ok else "FAIL", "checks": checks, "filled": v["filled"]}


def means(p: dict[str, Any]) -> dict[str, Any]:
    return {m: ({k: p[m].get(k) for k in ("n", "mean", "total", "ex_top3", "ci90_lo_trade")} if p[m].get("n") else {"n": 0}) for m in ("flat", "press")}


# ------------------------------------------------------------------------------------------------------------------------------ the read
def score(dec: dict[str, Any], clock: datetime, now: datetime, price_fn: Callable[..., dict[str, Any]], rows_after_drop: int | None,
          rows_after_drop_explained: str | None = None, damm: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pure scoring on loaded decisions (no I/O). Refuses before B, on unknown or unexplained rows_after_drop. Returns the full result dict."""
    b = boundary([x["bt"] for x in dec["decisions"]], clock)
    if b is None:
        raise ScoreRefused(f"no read boundary yet: fewer than {MIN_TRADES} decisions on {MIN_DAYS} distinct UTC dates")
    if now < b["B"]:
        raise ScoreRefused(f"before the read boundary B {b['B_utc']} (now {now:%Y-%m-%dT%H:%M:%SZ})")
    if rows_after_drop is None:
        raise ScoreRefused("rows_after_drop is unknown (pass the runner's status.json); it must be 0 or explained")
    if rows_after_drop != 0 and not (rows_after_drop_explained or "").strip():
        raise ScoreRefused(f"rows_after_drop = {rows_after_drop} without an explanation")
    items = [(x["key"], x["bt"]) for x in dec["decisions"] if x["bt"] < b["B"].timestamp()]
    p19 = price_fn(items, 1.9)
    p25 = price_fn(items, 2.5)
    nf = p19["counts"]["key_not_found_full_loss"] / max(len(items), 1)
    res: dict[str, Any] = {
        "schema": SCHEMA, "cell": "m10 (P18 paper copy)", "disclosure": DISCLOSURE, "clock_utc": f"{clock:%Y-%m-%dT%H:%M:%SZ}", "boundary": {k: v for k, v in b.items() if k != "B"},
        "log": {"head": dec["head"], "last_seq": dec["last_seq"], "start_rows": dec["start_rows"], "restarts": dec["restarts"]},
        "rows_after_drop": {"value": rows_after_drop, "explained": rows_after_drop_explained},
        "deciding": {"n_decisions": len(items), "md5_keys": hashlib.md5("\n".join(sorted(k for k, _ in items)).encode()).hexdigest(), "primary_1p9s": p19,
                     "entry_2p5s": {m: p25[m] for m in ("flat", "press")}, "not_found_share": nf,
                     "attempts_by_day": {u: sum(1 for _, t in items if S.day_of(t) == u) for u in sorted({S.day_of(t) for _, t in items})}},
        "strand_rule": "DECIDING: full loss (stake + buy send + stranded rent); the immediate-DAMM-sell line below is REPORT-ONLY",
    }
    rep: dict[str, Any] = {"REPORT_ONLY": True}
    rep["fixed_k9"] = means(price_fn(items, 1.9, fixed_k=FIXED_K))
    rep["entry_3p4s"] = means(price_fn(items, 3.4))
    rep["exit_3p4s"] = means(price_fn(items, 1.9, lat_out=3.4))
    rep["strand_immediate_damm_sell"] = strand_line(int(p19.get("strands", 0)), damm)
    res["report_only"] = rep
    if nf > X.MAX_NOT_FOUND:
        res.update({"verdict": "NOT_DECIDABLE", "reason": f"decisions not found in the day backfill {nf:.4f} > {X.MAX_NOT_FOUND} (AM2)"})
        return res
    if not p19["flat"].get("n"):
        res.update({"verdict": "NOT_DECIDABLE", "reason": "no priced decisions"})
        return res
    g = gate(p19, p25)
    res.update({"verdict": g["verdict"], "checks": g["checks"]})
    if p19.get("fills", 0) < MIN_TRADES:
        res["verdict"], res["reason"] = "NOT_DECIDABLE", f"fewer than {MIN_TRADES} fills"
    return res


def write(out_dir: Path, res: dict[str, Any]) -> str:
    txt = json.dumps(res, indent=1, default=float)
    (out_dir / OUT_JSON).write_text(txt)
    md = [f"# P18 scorer ({res.get('clock_utc')} clock)", "", f"verdict: **{res['verdict']}**", "", f"- {DISCLOSURE}"]
    if "deciding" in res:
        d = res["deciding"]
        p = d["primary_1p9s"]
        md.append(f"- boundary B {res['boundary']['B_utc']} ({res['boundary']['n']} decisions on {len(res['boundary']['dates'])} dates); priced {p['n_priced']}, fills {p.get('fills')}, strands {p.get('strands')}")
        for nm in ("flat", "press"):
            s = p[nm]
            if s.get("n"):
                md.append(f"- {nm}: n {s['n']} mean {s['mean']:.6f} total {s['total']:.4f} ex-top-3 {s['ex_top3']} CI90 lo trade {s['ci90_lo_trade']} days {s['days_pos']}/{s['days']}; "
                          f"2.5 s entry mean {d['entry_2p5s'][nm].get('mean')}")
        md.append(f"- checks: {json.dumps(res.get('checks'))}")
        sl = res["report_only"]["strand_immediate_damm_sell"]
        md.append(f"- REPORT-ONLY immediate DAMM sell covers {sl['strands_covered']} of {sl['strands_total']} strands (deciding line: full loss)")
    if res.get("reason"):
        md.append(f"- reason: {res['reason']}")
    md.append(f"- sha256 of {OUT_JSON}: {hashlib.sha256(txt.encode()).hexdigest()}")
    (out_dir / OUT_MD).write_text("\n".join(md) + "\n")
    return "\n".join(md)


def default_load(root: str, clock: datetime, B: datetime, base_map: str, fetch: Callable[[list[str]], dict[str, Any]]) -> dict[str, Any]:
    """Real-tape loader: day backfill hours [clock hour, B) under `root` through the scoped window guard, config map as in the m10 read (R4). Not exercised by the unit
    tests (it needs a real P18 backfill, which cannot exist before the m10 read)."""
    start = clock.replace(minute=0, second=0, microsecond=0)
    L.enable_swap3_pricing()
    with p18_window(root, start, B):
        counts = R.scan_configs(root)
        final, rep = R.resolve_configs(counts, json.loads(Path(base_map).read_text()), fetch)
        tmp = Path(root).parent / "p18_cfg_map_final.json"
        tmp.write_text(json.dumps(final, sort_keys=True))
        L.load_config_thresholds(str(tmp))
        L.CFG_UNMAPPED.clear()
        L.CFG_UNMAPPED.update(rep["unmapped"])
        if rep["unmapped_row_share"] > R.MAX_UNMAPPED_ROW_SHARE:
            raise ScoreRefused(f"unmapped config rows {rep['unmapped_row_share']:.3f} > {R.MAX_UNMAPPED_ROW_SHARE}")
        return S.load_exploration(root, start=start, end=B)  # the window globals are restored on exit; D is fully loaded here


def run(log_root: str, clock: datetime, out_dir: Path, now: datetime | None = None, status_json: str | None = None, explained: str | None = None,
        marker: str | None = None, price_fn_factory: Callable[[dict[str, Any], datetime, datetime], Callable[..., dict[str, Any]]] | None = None,
        load_fn: Callable[[datetime, datetime], dict[str, Any]] | None = None, damm: list[dict[str, Any]] | None = None) -> int:
    """All refusals happen before the marker is claimed. Exit codes: 0 read taken, 3 output exists, 4 marker exists, 6 refused (clock/boundary/drop), 7 setup."""
    now = now or datetime.now(timezone.utc)
    marker = marker or MARKER
    try:
        dec = load_decisions(log_root, clock)
        b = boundary([x["bt"] for x in dec["decisions"]], clock)
        if b is None or now < b["B"]:
            raise ScoreRefused("before the read boundary B" if b else "no read boundary yet")
        rad = None
        if status_json:
            rad = json.loads(Path(status_json).read_text()).get("stats", {}).get("rows_after_drop")
        if rad is None:
            raise ScoreRefused("rows_after_drop is unknown (pass the runner's status.json); it must be 0 or explained")
        if rad != 0 and not (explained or "").strip():
            raise ScoreRefused(f"rows_after_drop = {rad} without an explanation")
    except ScoreRefused as e:
        print(f"REFUSED (marker NOT claimed): {e}", file=sys.stderr)
        return 6
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"REFUSED: output already exists in {out_dir} (one look only)", file=sys.stderr)
        return 3
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(out_dir, os.W_OK):
            raise OSError(f"out dir not writable: {out_dir}")
    except Exception as e:  # noqa: BLE001
        print(f"REFUSED (setup, marker NOT claimed): {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return 7
    if not R.claim_marker(marker):
        print(f"REFUSED: one-look marker {marker} exists", file=sys.stderr)
        return 4
    print("LOOK CONSUMED: P18 marker claimed", flush=True)
    assert load_fn is not None and price_fn_factory is not None, "wiring"
    try:
        D = load_fn(clock, b["B"])
        res = score(dec, clock, now, price_fn_factory(D, clock, b["B"]), rad, explained, damm)
    except ScoreRefused as e:
        res = {"schema": SCHEMA, "verdict": "NOT_DECIDABLE", "reason": f"refused after the claim: {e}", "disclosure": DISCLOSURE, "clock_utc": f"{clock:%Y-%m-%dT%H:%M:%SZ}"}
    print(write(out_dir, res))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log-root", required=True)
    ap.add_argument("--clock", required=True, help="P18 clock, UTC, e.g. 2026-11-02T00:00:00Z")
    ap.add_argument("--counts-only", action="store_true", help="dry score: decision counts per date and the boundary; no pricing, no marker")
    ap.add_argument("--out")
    ap.add_argument("--backfill-root")
    ap.add_argument("--status-json")
    ap.add_argument("--rows-after-drop-explained")
    ap.add_argument("--base-map", default=R.BASE_MAP)
    ap.add_argument("--now", help="override (tests)")
    a = ap.parse_args(argv)
    clock = parse_utc(a.clock)
    if a.counts_only:
        try:
            print(json.dumps(counts_only(a.log_root, clock), indent=1))
        except ScoreRefused as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 6
        return 0
    if not (a.out and a.backfill_root):
        ap.error("--out and --backfill-root are required for a read")
    root = a.backfill_root
    fetch = R.helius_fetch()
    return run(a.log_root, clock, Path(S.check_out_dir(a.out)), parse_utc(a.now) if a.now else None, a.status_json, a.rows_after_drop_explained,
               load_fn=lambda c, B: default_load(root, c, B, a.base_map, fetch), price_fn_factory=lambda D, c, B: make_price_fn(D))


if __name__ == "__main__":
    raise SystemExit(main())
