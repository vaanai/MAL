"""R-EQ of the A-R tip-replay heal on REAL exploration-block tape hours (QP verdict on PR #1232, item 5 (a)-(f); QP-RULING-1104-oc-feedfix s3.3 test 2), plus the
heal's trigger-path latency (item 9). COUNTS ONLY: no outcome, price, mint, pool or signature is printed.

Allowed data (hunt23 RULES.md, "OUTCOMES (prices after a decision) only on these exploration blocks": /data/mal/clean-view/explore-0814 (w1..w7),
/data/mal/clean-view/oracle-insample-2026-09-22_25, /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00, /data/mal/clean-view/oracle-live-2026-09-25_27).
This tool only opens `trades-<hour>.jsonl.zst` and `migrations-<hour>.jsonl.zst` under a block dir whose path contains `explore-0814`, `oracle-insample-2026-09-22_25`
or `fast-pool-2026-09-18T23_2026-09-22T00`; it refuses any hour >= 2026-10-01T00 and any hour in the D2/D3 domain [2026-10-02T10, 2026-10-16T01), the arena tape,
the tip tape, any `forward-`/`truth-`/`fresh-08`/`exp011-` path. The V map is the arena_screen default (pool_v_0814, pool_v_0909).

Per hour: all V-range (17.5e9..17.7e9) canonical (one pool per mint) PumpSwap WSOL pools are interleaved in tape order (slot, tx_index, event_index) through ONE stock
engine (tools.h5_shadow.Engine, sps_fn=None: the live SlotClock path, PreEventClassifier) and ONE heal engine (tools.h5_lane_heal HealEngine, same rows; the same rows
are also fed as tip rows through ReplayTip(0) -> on_tip_row). Pools are announced (`on_create_pool`) one slot before their first print in the hour when their mint
has a `complete` migration row in the hour's migrations file (the hour's create rows), or, with --announce first_print, at every V-range pool's first print.
Reported: stock triggers n; heal-engine triggers n; the heal's CONTROL replay matches n (variant, signature, event_index, slot, boost_src, s0, v0 all equal; the
engine's own `_lh_replay` check) and misses by reason code; heal statuses; md5 of the stock record stream vs the heal-stripped stream (lane_heal / lane_gap_seq
removed); and the heal's wall time per trigger (p50 / p90 / max ms). Any miss is reported, never loosened.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator, Sequence

from tools import h5_lane_heal as hl
from tools import h5_shadow as hs

ALLOWED_BLOCK_MARKS = ("explore-0814", "oracle-insample-2026-09-22_25", "fast-pool-2026-09-18T23_2026-09-22T00")
FORBIDDEN_MARKS = ("forward-", "truth-", "fresh-08", "exp011-", "arena", "tip", "shadow", "probe", "canary", "meta-capture", "/var/lib/mal", "fwdv1")
D23 = ("2026-10-02T10", "2026-10-16T01")
MIN_TRIGGERS = 10  # QP condition (e)
HOUR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")


class ReqRefused(Exception):
    pass


def check_inputs(block_dirs: Sequence[str], hours: Sequence[str]) -> None:
    for b in block_dirs:
        if any(m in b for m in FORBIDDEN_MARKS) or not any(m in b for m in ALLOWED_BLOCK_MARKS):
            raise ReqRefused("block dir is not an allowed exploration block (hunt23 RULES.md OUTCOMES list)")
    for h in hours:
        if not HOUR_RE.match(h):
            raise ReqRefused("bad hour")
        if h >= "2026-10-01T00" or D23[0] <= h < D23[1]:
            raise ReqRefused("hour outside the exploration blocks (October / D2-D3)")


def zlines(path: str) -> Iterator[bytes]:
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, bufsize=1 << 20)
    assert p.stdout is not None
    yield from p.stdout
    if p.wait() != 0:
        raise ReqRefused("zstd failed")


def find(block_dirs: Sequence[str], kind: str, hour: str) -> str | None:
    for b in block_dirs:
        for root in (os.path.join(b, kind), *(os.path.join(b, w, kind) for w in sorted(os.listdir(b)) if os.path.isdir(os.path.join(b, w)))):
            f = os.path.join(root, f"{kind}-{hour}.jsonl.zst")
            if os.path.isfile(f):
                return f
    return None


def load_hour(block_dirs: Sequence[str], hour: str, vmap: Any) -> tuple[list[dict], set[str]]:
    """V-range canonical PumpSwap WSOL rows in tape order, and the mints that have a `complete` migration row in the hour."""
    tf = find(block_dirs, "trades", hour)
    if tf is None:
        raise ReqRefused("no trades file for the hour in the given blocks")
    rows: list[dict] = []
    for line in zlines(tf):
        if b'"venue":"pumpswap"' not in line:
            continue
        r = json.loads(line)
        pool = r.get("pool")
        if r.get("venue") != "pumpswap" or not pool or r.get("slot") is None or r.get("side") not in ("buy", "sell") or not r.get("quote_is_wsol"):
            continue
        bt = r.get("block_time") or r.get("event_ts")
        v = vmap.get(pool)
        if not isinstance(bt, (int, float)) or bt <= 0 or v is None or not (hs.V_LO <= v <= hs.V_HI):
            continue
        rows.append({"venue": "pumpswap", "pool": pool, "slot": int(r["slot"]), "side": r["side"], "trader": r.get("trader"), "sol_lamports": int(r["sol_lamports"]),
                     "token_raw": int(r["token_raw"]), "quote_reserve": int(r["quote_reserve"]), "base_reserve": int(r["base_reserve"]),
                     "virtual_quote_reserves": int(v), "event_ts": int(bt), "t_recv_ms": int(bt) * 1000, "signature": r.get("signature"), "mint": r.get("mint"),
                     "event_index": int(r.get("event_index") or 0), "tx_index": int(r.get("tx_index") or 0)})
    mints_by_pool: dict[str, set] = collections.defaultdict(set)
    for r in rows:
        mints_by_pool[r["pool"]].add(r["mint"])
    pools_of_mint: dict[str, set] = collections.defaultdict(set)
    for p, ms in mints_by_pool.items():
        for m in ms:
            pools_of_mint[m].add(p)
    canon = {p for p, ms in mints_by_pool.items() if len(ms) == 1 and all(len(pools_of_mint[m]) == 1 for m in ms)}
    rows = [r for r in rows if r["pool"] in canon]
    rows.sort(key=lambda r: (r["slot"], r["tx_index"], r["event_index"]))
    migrated: set[str] = set()
    mf = find(block_dirs, "migrations", hour)
    if mf is not None:
        for line in zlines(mf):
            if b'"type":"complete"' in line:
                try:
                    m = json.loads(line).get("mint")
                except ValueError:
                    continue
                if m:
                    migrated.add(m)
    return rows, migrated


def run_engine(eng: Any, rows: list[dict], migrated: set[str], announce: str, tip: Any = None) -> None:
    announced: set[str] = set()
    for row in rows:
        pool = row["pool"]
        if pool not in announced and (announce == "first_print" or row["mint"] in migrated):
            announced.add(pool)
            eng.on_create_pool(pool, row["mint"], hs.WSOL_MINT, row["slot"] - 1, row["t_recv_ms"])
        eng.wall = lambda ms=row["t_recv_ms"]: ms
        eng.on_trade(dict(row))
        if tip is not None:
            tip.push(dict(row))
            tip.release(eng)
    if tip is not None:
        tip.release(eng, final=True)
    eng.close_all("replay_end")


def hk(out: dict) -> str:
    return f"{out.get('status')}/{out.get('reason')}"


def timed_class(base: type, times: list, calls: list, forced: list | None = None) -> type:
    """`times`: the heal's own wall time per trigger (the force-run below is outside the timed span). `calls`: reason codes of the replays the heal ran itself.
    `forced` (R-EQ mode, default in main): for EVERY trigger, whatever its heal status, the per-pool replay is run (the heal's own run, or a forced one when the
    heal did not reach it) and (heal status, reason code) is appended. The heal decision itself is unchanged: the forced result is only recorded."""

    class Timed(base):  # type: ignore[valid-type,misc]
        _req_last: Any = None

        def _lh_heal(self, rec: dict) -> dict:
            self._req_last = None
            t0 = time.perf_counter()
            try:
                out = super()._lh_heal(rec)
            except Exception:
                if forced is not None:
                    forced.append(("raised/None", "heal_raised"))  # a miss: the engine swallows the exception and the trigger would otherwise go uncounted
                raise
            finally:
                times.append((time.perf_counter() - t0) * 1000.0)
            if forced is not None:
                res = self._req_last
                if res is None:  # the heal refused before its replay: run it anyway, record only
                    p, st = self.pools.get(rec.get("pool")), self._lh_pools.get(rec.get("pool"))
                    if p is None or st is None:
                        forced.append((hk(out), "no_store"))
                        return out
                    try:
                        m = self._lh_merge(st, int(rec["slot"]), p.mint)
                        res = super()._lh_replay(p, st, m, rec)
                    except Exception:  # noqa: BLE001
                        forced.append((hk(out), "replay_error"))
                        return out
                forced.append((hk(out), "match" if res[0] is None and res[1].get("match") is True else (res[0] or "null_match")))
            return out

        def _lh_replay(self, p, st, m, rec):  # noqa: ANN001, ANN202
            res = super()._lh_replay(p, st, m, rec)
            self._req_last = res
            calls.append(res[0] if res[0] is not None else ("match" if res[1].get("match") is True else "null_match"))
            return res

    return Timed


def strip(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in ("lane_heal", "lane_gap_seq")}


def md5s(records: list[dict], strip_heal: bool) -> tuple[str, str]:
    a, b = hashlib.md5(), hashlib.md5()
    for r in records:
        r = strip(r) if strip_heal else r
        line = json.dumps(r, sort_keys=True, separators=(",", ":"), default=str).encode()
        a.update(line)
        if r.get("type") not in ("outcome", "outcome_bx10", "strip"):
            b.update(line)
    return a.hexdigest(), b.hexdigest()


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * len(s)))], 3)


def lat(xs: list[float]) -> dict:
    return {"n": len(xs), "p50_ms": pct(xs, 0.5), "p90_ms": pct(xs, 0.9), "max_ms": round(max(xs), 3) if xs else None}


def verdict_of(stock_n: int, heal_n: int, replays: int, matches: int, streams_equal: bool, min_triggers: int = MIN_TRIGGERS) -> str:
    """`R-EQ PASS`, or `R-EQ FAIL <reason>` (first failing check): too few stock triggers (incl. 0), heal trigger count != stock, forced replays short of the
    stock triggers (e.g. the heal raised), any miss, or the record streams differ."""
    if stock_n < min_triggers:
        return f"R-EQ FAIL stock_triggers_below_{min_triggers}"
    if heal_n != stock_n:
        return "R-EQ FAIL heal_trigger_count_differs"
    if replays < stock_n:
        return "R-EQ FAIL forced_replays_short"
    if matches != replays:
        return "R-EQ FAIL replay_miss"
    if not streams_equal:
        return "R-EQ FAIL streams_differ"
    return "R-EQ PASS"


def req_hours(block_dirs: Sequence[str], hours: Sequence[str], announce: str, vmap: Any, status_path: str, min_triggers: int = MIN_TRIGGERS) -> dict:
    stock_n = heal_n = 0
    status: collections.Counter = collections.Counter()
    reasons: collections.Counter = collections.Counter()
    misses: collections.Counter = collections.Counter()
    times: list[float] = []
    per_hour: dict[str, Any] = {}
    equal_all = True
    n_calls = n_match = 0
    forced: list = []
    rows_total = 0
    for h in hours:
        rows, migrated = load_hour(block_dirs, h, vmap)
        rows_total += len(rows)
        s_out: list[dict] = []
        stock = hs.Engine(s_out.append, sps_fn=None, classifier=hs.PreEventClassifier())
        stock.tip = hs.ReplayTip(0)  # the same tip rows, so the stock records carry the same report-only union fields
        run_engine(stock, rows, migrated, announce, tip=stock.tip)
        calls: list = []
        h_out: list[dict] = []
        heal = timed_class(hl.build_engine_class(status_path), times, calls, forced)(h_out.append, sps_fn=None, classifier=hs.PreEventClassifier())
        heal.tip = hs.ReplayTip(0)
        run_engine(heal, rows, migrated, announce, tip=heal.tip)
        st_tr = [r for r in s_out if r["type"] == "trigger"]
        h_tr = [r for r in h_out if r["type"] == "trigger"]
        stock_n += len(st_tr)
        heal_n += len(h_tr)
        for r in h_tr:
            lh = r.get("lane_heal") or {}
            status[lh.get("status")] += 1
            reasons[f"{lh.get('status')}/{lh.get('reason')}"] += 1
        n_calls += len(calls)
        n_match += sum(1 for c in calls if c == "match")
        for c in calls:
            if c != "match":
                misses[c] += 1
        stream_eq = md5s(s_out, False) == md5s(h_out, True)
        equal_all = equal_all and stream_eq
        sm, sm_no = md5s(s_out, False)
        hm, hm_no = md5s(h_out, True)
        per_hour[h] = {"rows": len(rows), "pools_announced": len({r["pool"] for r in rows if announce == "first_print" or r["mint"] in migrated}),
                       "stock_triggers": len(st_tr), "heal_triggers": len(h_tr), "stock_md5": sm, "heal_stripped_md5": hm, "stock_md5_no_outcome": sm_no,
                       "heal_stripped_md5_no_outcome": hm_no, "streams_equal": stream_eq}
    return {"hours": list(hours), "announce": announce, "rows_total": rows_total, "stock_triggers": stock_n, "heal_triggers": heal_n,
            "replays_run": n_calls, "replay_matches": n_match,
            "misses_by_reason": dict(misses), "status": dict(status), "status_reason": dict(reasons), "all_streams_equal": equal_all,
            "req_all_triggers": {"replays": len(forced), "matches": sum(1 for _, c in forced if c == "match"),
                                 "misses_by_reason": dict(collections.Counter(c for _, c in forced if c != "match")),
                                 "by_heal_status_reason": {k: {"n": v, "matches": sum(1 for h_, c in forced if h_ == k and c == "match")}
                                                          for k, v in sorted(collections.Counter(h_ for h_, _ in forced).items())}},
            "heal_latency": lat(times), "per_hour": per_hour, "verdict": verdict_of(stock_n, heal_n, len(forced), sum(1 for _, c in forced if c == "match"),
                                                                                     equal_all, min_triggers)}


def synthetic_worst_case(n_filler: int = 19_900, path: str | None = None) -> dict:
    """One pool whose websocket store holds ~n_filler + 15 rows (under ROWS_CAP = 20,000): (1) a CONTROL replay of the whole store, (2) the gap path
    (3 hidden rows -> slot_jump -> merge + replay + coverage). Fixture rows from tools.test_h5_lane_heal; no tape, no network."""
    from tools.test_h5_lane_heal import FakeTip, Scn, mk, write_status
    from tools.test_h5_shadow import MINT, SOL

    out: dict[str, Any] = {"rows_cap": hl.ROWS_CAP}
    with tempfile.TemporaryDirectory() as d:
        sp = path or os.path.join(d, "status.json")
        for name, gap in (("control", False), ("gap_path", True)):
            write_status(sp, 1050)
            s = Scn()
            P = s.pda
            s.row(1000, "buy", "A", SOL // 10)
            for sl in (1020, 1045, 1070):
                s.row(sl, "buy", P, SOL // 2)
            s.row(1095, "buy", "B", SOL // 20)
            if gap:
                s.row(1100, "buy", "C", SOL // 10, hide=True)
                s.row(1110, "buy", P, SOL // 2, hide=True)
                s.row(1130, "sell", P, SOL // 10, hide=True)
            lo, span = (1140, 55) if gap else (1096, 99)  # control: no slot jump anywhere (no flagged gap), so the CONTROL replay runs over the whole store
            for i in range(n_filler):
                s.row(lo + (i * span) // n_filler, "buy", f"T{i % 5000}", 1_000_000)
            q_tot = s.t.q + s.t.v
            s.row(1200, "sell", "DUMPER", int(q_tot - 35 * SOL))
            s.finish()
            times: list[float] = []
            eng, rec = mk(hl.build_engine_class(sp), sp, FakeTip(hw_slot=10**9))
            eng.__class__ = timed_class(eng.__class__, times, [])
            eng.on_create_pool(s.pool, MINT, hs.WSOL_MINT, 999, 0)
            ws = s.ws_rows()
            for r in ws[:-1]:
                eng.on_trade(dict(r))
            write_status(sp, 1170, 0)
            for r in s.tip_rows():
                eng.on_tip_row(r)
            eng.on_trade(dict(ws[-1]))
            tr = [r for r in rec if r["type"] == "trigger"]
            out[name] = {"triggers": len(tr), "heal_ms": [round(t, 3) for t in times], "ws_rows": len(ws),
                         "status": sorted({f"{r['lane_heal']['status']}/{r['lane_heal']['reason']}" for r in tr})}
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--block", action="append", default=[], help="an allowed exploration block dir (explore-0814 or one of its w*, oracle-insample..., fast-pool...)")
    ap.add_argument("--hours", default="", help="comma-separated tape hours YYYY-MM-DDTHH")
    ap.add_argument("--announce", choices=("migration", "first_print"), default="migration")
    ap.add_argument("--vmap", nargs="*", default=None)
    ap.add_argument("--synthetic-worst-case", action="store_true")
    a = ap.parse_args(argv)
    res: dict[str, Any] = {}
    try:
        if a.hours:
            hours = [h.strip() for h in a.hours.split(",") if h.strip()]
            check_inputs(a.block, hours)
            from tools import arena_screen as S

            vmap = S.VMap(a.vmap if a.vmap else list(S.VMAP_DEFAULT))
            with tempfile.TemporaryDirectory() as d:
                res["real"] = req_hours(a.block, hours, a.announce, vmap, os.path.join(d, "no-status.json"))
        if a.synthetic_worst_case:
            res["synthetic_worst_case"] = synthetic_worst_case()
    except ReqRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1, sort_keys=True))
    v = res.get("real", {}).get("verdict")
    if v is not None:
        print(v)
        if v != "R-EQ PASS":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
