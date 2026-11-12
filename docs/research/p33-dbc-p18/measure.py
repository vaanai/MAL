#!/usr/bin/env python3
"""P33 measurement (docs/research/p33-dbc-p18/DECLARE.md). PAPER ONLY. Counts and decode only: NOTHING IS PRICED (no fill, no fee-adjusted return, no P&L).

Hours: DBC exploration [2026-10-14T00, 2026-10-17T00) only, read through the tools.dbc_price data guard (default window, default root). DAMM v2 pool files fetched by P16 for
migrations inside that window; rows at/after 2026-10-17T00Z are dropped here again. No RPC, no key, 0 credits.

    python3 docs/research/p33-dbc-p18/measure.py engine  --out DIR     # M-c: one causal dbc_fwd.Engine run (live policy, no oracle) over the 72 h -> DIR/declog, DIR/engine.json
    python3 docs/research/p33-dbc-p18/measure.py measure --out DIR     # M-a, M-b, M-c tables from DIR/declog + tape + DAMM files -> DIR/measure.json
    python3 docs/research/p33-dbc-p18/measure.py all     --out DIR
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from tools import dbc_fwd as W  # noqa: E402
from tools import dbc_price as L  # noqa: E402
from tools import dbc_screen as S  # noqa: E402

START = datetime(2026, 10, 14, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 17, 0, tzinfo=timezone.utc)
SW3_BT = datetime(2026, 10, 16, 4, tzinfo=timezone.utc).timestamp()  # EvtSwap3 for every swap from 2026-10-16T04Z (dbc_price.enable_swap3_pricing)
END_BT = END.timestamp()
DAMM_DIR = "/data2/mal/venue-tape-backfill/meteora_damm_v2"
CFG = str(ROOT / "docs/research/dbc-p17/data/cfg_summary.json")
STRAND_POOLS = str(ROOT / "docs/research/dbc-p16/strand_pools.json")
AM5_DECLOG = "/data/mal/hunt-1008/push-1016/hunt29/dbc/am5-replay-7d5be9e5/declog"  # causal live-policy log of [10-14T00, 10-16T04) at the am1 tag (sanity only)
FORBIDDEN = ("fwdv1", "meteora_dbc_holdout", "p17_union", "meta-capture", "holdout-read", "fwd-read", "decode-check-1017")
NS = (0, 1, 2, 5, 10, 30, 60, 300, 3600)
QS = (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)


def refuse_paths(*ps: str) -> None:
    for p in ps:
        rp = os.path.realpath(p)
        if any(f in p or f in rp for f in FORBIDDEN):
            raise SystemExit(f"REFUSED sealed/spent path: {p}")


def q(a: list[float] | np.ndarray) -> dict[str, Any]:
    a = np.asarray([x for x in a if x is not None and np.isfinite(x)], float)
    if not len(a):
        return {"n": 0}
    return {"n": int(len(a)), "mean": float(a.mean()), **{f"p{int(x * 100)}": float(np.quantile(a, x)) for x in QS}, "min": float(a.min()), "max": float(a.max())}


def sha(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# --------------------------------------------------------------------------------------------------------------------------------- M-c engine
def run_engine(out: Path) -> dict[str, Any]:
    from tools.arena_declog import DecisionLog  # noqa: PLC0415

    L.enable_swap3_pricing()
    n_cfg = L.load_config_thresholds(CFG)
    log_root = out / "declog"
    if log_root.exists() and any(log_root.rglob("*.jsonl")):
        raise SystemExit(f"REFUSED: {log_root} not empty")
    log = DecisionLog(str(log_root))
    first_ok = START.timestamp() + S.WARMUP_HOURS * 3600
    live_ss: dict[int, float] = {}
    eng = W.Engine(first_ok, ss_live=lambda hk, m=live_ss: S.slot_s_for(m, hk), screen_policy=False, log=log, meta={"bt_src": "tape_block_time"})
    timing: list[dict[str, Any]] = []
    orig = eng._decide

    def timed(p, lo_new, hi):  # noqa: ANN001
        n0 = len(eng.decisions_live)
        t0 = time.perf_counter()
        orig(p, lo_new, hi)
        dt = time.perf_counter() - t0
        if len(eng.decisions_live) > n0:
            timing.append({"key": eng.decisions_live[-1]["key"], "decide_s": dt})

    eng._decide = timed  # measurement wrapper only: same call, same arguments
    log.append(W.BOOK, int(START.timestamp() * 1000), {"type": "start", "book": W.BOOK, "mode": "p33-measure", "window": [f"{START:%Y-%m-%dT%H}", f"{END:%Y-%m-%dT%H}"],
                                                       "cell": "m10", "exit_rule": W.EXIT_RULE, "code_sha256": W.code_shas(), "schema": W.SCHEMA})
    t0 = time.time()
    hs = W.stream_hours(eng, START, END, None, live_ss, log)
    ru = resource.getrusage(resource.RUSAGE_SELF)
    res = {"hours_streamed": hs, "decisions_live": len(eng.decisions_live), "stats": eng.stats, "cfg_thresholds": n_cfg, "live_ss": {str(k): v for k, v in live_ss.items()},
           "decide_timing": timing, "wall_s": round(time.time() - t0, 1), "peak_rss_mib": round(ru.ru_maxrss / 1024, 1), "code_sha256": W.code_shas(),
           "declog": {"md5": log.md5(), "head": list(log.verify(W.BOOK))}, "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out / "engine.json").write_text(json.dumps(res, indent=1, default=float))
    return res


# --------------------------------------------------------------------------------------------------------------------------------- tape pass
def tape_pass() -> dict[str, Any]:
    """One pass over the 72 exploration hours through the dbc_price guard. Keeps: slots that advance the engine (usable trade rows and curve_complete rows), per hour slot_s
    (dbc_fwd.slot_s_of over usable trades), first curve_complete per pool, Swap3 flag per pool, per pool BUY prints (slot, signer) for the follower proxy, MigrationDammV2 ix slots."""
    adv: set[int] = set()
    comp: dict[str, tuple[int, float]] = {}
    sw3: set[str] = set()
    buys: dict[str, list[tuple[int, str]]] = defaultdict(list)
    first_trade: dict[str, float] = {}
    slot_s: dict[int, float] = {}
    hours, incomplete = 0, []
    n_rows = Counter()
    for h in L.exploration_hours(START, END):
        f = L.hour_file(h)
        refuse_paths(f)
        if not os.path.exists(f):
            incomplete.append(f"{h:%Y-%m-%dT%H} missing")
            continue
        hours += 1
        bts, sls, ended = [], [], False
        for line in L._zstd_lines(f, None):
            if line.startswith(b'{"v":1,"venue":"meteora_dbc","type":"backfill_hour_end"'):
                ended = True
                continue
            r = json.loads(line)
            t = r.get("type")
            n_rows[t] += 1
            if t == "trade":
                if not L.usable_row(r):
                    continue
                s = int(r["slot"])
                adv.add(s)
                bts.append(L.block_time_s(r))
                sls.append(s)
                p = r["pool"]
                if r.get("event") == "EvtSwap3":
                    sw3.add(p)
                first_trade.setdefault(p, L.block_time_s(r))
                if r.get("side") == "buy":
                    buys[p].append((s, r.get("signer") or ""))
            elif t == "curve_complete":
                s = int(r["slot"])
                adv.add(s)
                p = r["pool"]
                if p not in comp or s < comp[p][0]:
                    comp[p] = (s, L.block_time_s(r))
        if not ended:
            incomplete.append(f"{h:%Y-%m-%dT%H} no end row")
        v = W.slot_s_of(np.array(bts), np.array(sls)) if bts else None
        if v is not None:
            slot_s[int(h.timestamp() // 3600)] = v
    return {"adv": np.array(sorted(adv), np.int64), "comp": comp, "sw3": sw3, "buys": buys, "first_trade": first_trade, "slot_s": slot_s, "hours": hours,
            "incomplete": incomplete, "row_types": dict(n_rows)}


# --------------------------------------------------------------------------------------------------------------------------------- M-b DAMM
def init_fields(payload: bytes) -> dict[str, int]:
    """EvtInitializePool tail (cp-amm IDL order: ..., activation_type u8, collect_fee_mode u8, liquidity u128, sqrt_price u128, activation_point u64, token_a_flag u8,
    token_b_flag u8, token_a_amount u64, token_b_amount u64, total_amount_a u64, total_amount_b u64, pool_type u8). sqrt_price/liquidity offsets = damm_decode.init_state."""
    o = len(payload)
    return {"activation_type": payload[o - 77], "collect_fee_mode": payload[o - 76], "liquidity": int.from_bytes(payload[o - 75:o - 59], "little"),
            "sqrt_price": int.from_bytes(payload[o - 59:o - 43], "little"), "activation_point": int.from_bytes(payload[o - 43:o - 35], "little"), "pool_type": payload[o - 1]}


def load_damm(pool: str) -> list[dict[str, Any]] | None:
    f = Path(DAMM_DIR) / "pools" / f"pool-{pool}.jsonl.zst"
    if not f.is_file():
        return None
    raw = subprocess.run(["zstd", "-dc", "-q", str(f)], capture_output=True, check=True).stdout
    return [json.loads(x) for x in raw.splitlines() if x]


def sell_rate(r: dict[str, Any]) -> float | None:
    fee = r["trading_fee"] + r["protocol_fee"] + r["compounding_fee"] + r["referral_fee"]
    g = r["amount_out"] + fee
    return fee / g if g else None


def damm_measure(tp: dict[str, Any]) -> dict[str, Any]:
    ck = json.loads((Path(DAMM_DIR) / "checkpoint.json").read_text())
    strand = set(json.loads(Path(STRAND_POOLS).read_text())["pools"])
    mig_by_dbc: dict[str, dict[str, Any]] = {}
    mig_status = Counter()
    for sig, m in ck["migs"].items():
        mig_status[m.get("status")] += 1
        if m.get("status") == "ok" and m.get("dbc_pool"):
            d = m["dbc_pool"]
            if d not in mig_by_dbc or m["slot"] < mig_by_dbc[d]["slot"]:
                mig_by_dbc[d] = {**m, "signature": sig}
    fetched = set(ck["pools"])
    comp = tp["comp"]
    per: list[dict[str, Any]] = []
    checks = Counter()
    for dbc_pool, (cs, cbt) in comp.items():
        e: dict[str, Any] = {"dbc_pool": dbc_pool, "comp_slot": cs, "comp_bt": cbt, "era": "swap3" if cbt >= SW3_BT else "swap2", "sw3_pool": dbc_pool in tp["sw3"]}
        m = mig_by_dbc.get(dbc_pool)
        if m is None:
            e["status"] = "no_migration_in_index"
            per.append(e)
            continue
        e.update(mig_slot=int(m["slot"]), mig_bt=float(m["block_time"]), damm_pool=m["damm_pool"], wsol_is_b=bool(m.get("wsol_is_b")),
                 mig_dslot=int(m["slot"]) - cs, mig_ds=float(m["block_time"]) - cbt, slot_s_mig=S.slot_s_for(tp["slot_s"], int(float(m["block_time"]) // 3600)))
        dp = m["damm_pool"]
        e["sample"] = "strand" if dbc_pool in strand or dp in strand else "uniform"
        if dp not in fetched:
            e["status"] = "not_fetched"
            per.append(e)
            continue
        rows = load_damm(dp)
        if not rows:
            e["status"] = "no_file"
            per.append(e)
            continue
        ini = next((r for r in rows if r.get("event") == "EvtInitializePool"), None)
        tr = [r for r in rows if r.get("type") == "trade" and not r.get("failed") and float(r["block_time"]) < END_BT]
        e["dropped_after_cut"] = sum(1 for r in rows if r.get("type") == "trade" and float(r["block_time"]) >= END_BT)
        meta = rows[0] if rows[0].get("type") == "pool_meta" else {}
        e["truncated"] = bool(meta.get("truncated"))
        e["covered_to_bt"] = min(float(meta.get("covered_to_bt") or END_BT), END_BT)
        if ini is not None:
            fi = init_fields(base64.b64decode(ini["payload_b64"]))
            e.update(init_slot=int(ini["slot"]), act_type=int(fi["activation_type"]), act_point=int(fi["activation_point"]), init_cfm=int(fi["collect_fee_mode"]), pool_type=int(fi["pool_type"]))
            if fi["activation_type"] == 0:
                e["act_minus_mig"] = int(fi["activation_point"]) - int(m["slot"])
                e["act_unit"] = "slots"
            elif fi["activation_type"] == 1:
                e["act_minus_mig"] = int(fi["activation_point"]) - int(m["block_time"])
                e["act_unit"] = "s"
            if tr:
                checks["cfm_match" if int(tr[0]["collect_fee_mode"]) == int(fi["collect_fee_mode"]) else "cfm_mismatch"] += 1
            checks["init_slot_eq_mig" if int(ini["slot"]) == int(m["slot"]) else "init_slot_ne_mig"] += 1
        else:
            checks["no_init_event"] += 1
        sells = [r for r in tr if r.get("side") == "sell"]
        if tr:
            e.update(first_trade_dslot=int(tr[0]["slot"]) - cs, first_trade_ds=float(tr[0]["block_time"]) - cbt,
                     first_trade_after_mig_dslot=int(tr[0]["slot"]) - int(m["slot"]))
        if sells:
            s0 = sells[0]
            e.update(first_sell_dslot=int(s0["slot"]) - cs, first_sell_ds=float(s0["block_time"]) - cbt, first_sell_after_mig_dslot=int(s0["slot"]) - int(m["slot"]),
                     first_sell_fee=sell_rate(s0))
            w60 = [sell_rate(r) for r in sells if float(r["block_time"]) - float(m["block_time"]) <= 60]
            e["sell_fee_60s_median"] = float(np.median([x for x in w60 if x is not None])) if any(x is not None for x in w60) else None
            e["n_sells_60s"] = len(w60)
            for N in NS:
                e[f"sell_signers_le{N}s"] = len({r.get("signer") for r in sells if float(r["block_time"]) - cbt <= N})
        e["n_trades"] = len(tr)
        e["status"] = "ok"
        per.append(e)

    def table(sel: list[dict[str, Any]]) -> dict[str, Any]:
        mig = [e for e in sel if "mig_slot" in e]
        ok = [e for e in sel if e.get("status") == "ok"]
        t: dict[str, Any] = {"completions": len(sel), "with_migration": len(mig), "damm_ok": len(ok),
                             "status": dict(Counter(e.get("status") for e in sel)),
                             "comp_to_mig_s": q([e["mig_ds"] for e in mig]), "comp_to_mig_slots": q([e["mig_dslot"] for e in mig]),
                             "mig_same_slot_as_comp": sum(1 for e in mig if e["mig_dslot"] == 0),
                             "wsol_is_b": sum(1 for e in mig if e.get("wsol_is_b")),
                             "comp_to_first_trade_s": q([e.get("first_trade_ds") for e in ok if "first_trade_ds" in e]),
                             "comp_to_first_sell_s": q([e.get("first_sell_ds") for e in ok if "first_sell_ds" in e]),
                             "comp_to_first_sell_slots": q([e.get("first_sell_dslot") for e in ok if "first_sell_dslot" in e]),
                             "mig_to_first_sell_slots": q([e.get("first_sell_after_mig_dslot") for e in ok if "first_sell_after_mig_dslot" in e]),
                             "no_trade_before_cut": sum(1 for e in ok if "first_trade_ds" not in e), "no_sell_before_cut": sum(1 for e in ok if "first_sell_ds" not in e),
                             "truncated": sum(1 for e in ok if e.get("truncated")),
                             "activation_type": dict(Counter(e.get("act_type") for e in ok)),
                             "act_minus_mig_slots": q([e["act_minus_mig"] for e in ok if e.get("act_unit") == "slots"]),
                             "act_minus_mig_s": q([e["act_minus_mig"] for e in ok if e.get("act_unit") == "s"]),
                             "act_after_mig": sum(1 for e in ok if (e.get("act_minus_mig") or 0) > 0),
                             "first_sell_fee": q([e.get("first_sell_fee") for e in ok if e.get("first_sell_fee") is not None]),
                             "sell_fee_60s_median": q([e.get("sell_fee_60s_median") for e in ok if e.get("sell_fee_60s_median") is not None])}
        share = {}
        for N in NS:
            share[str(N)] = {"migration_landed": sum(1 for e in ok if e["mig_ds"] <= N),
                             "migration_landed_and_active": sum(1 for e in ok if e["mig_ds"] <= N and act_ok(e, N)),
                             "other_sell_landed": sum(1 for e in ok if (e.get("first_sell_ds") is not None and e["first_sell_ds"] <= N)),
                             "denominator_damm_ok": len(ok)}
        t["by_N_seconds_after_completion"] = share
        return t

    def act_ok(e: dict[str, Any], N: float) -> bool:
        if e.get("act_unit") == "s":
            return e["mig_ds"] + max(0, e["act_minus_mig"]) <= N
        if e.get("act_unit") == "slots":
            return e["mig_ds"] + max(0, e["act_minus_mig"]) * e["slot_s_mig"] <= N  # activation slots after the migration converted at the migration hour's slot_s
        return False

    out = {"migration_index_status": dict(mig_status), "migrations_ok_with_dbc_pool": len(mig_by_dbc), "completions_in_window": len(comp), "decode_checks": dict(checks)}
    groups = {"all": per, "uniform": [e for e in per if e.get("sample") == "uniform"], "strand": [e for e in per if e.get("sample") == "strand"]}
    for g in ("uniform", "all"):
        for era in ("swap2", "swap3"):
            groups[f"{g}_{era}"] = [e for e in groups[g] if e["era"] == era]
    out["tables"] = {k: table(v) for k, v in groups.items()}
    # feasibility bar (DECLARE.md): on U, completion->migration <= 60 s >= 90%; active at migration + k >= 90%; other-signer sell within 60 s >= 50%
    U = [e for e in groups["uniform"] if e.get("status") == "ok"]
    Um = [e for e in groups["uniform"] if "mig_slot" in e]
    k_s = 1.9
    b1 = sum(1 for e in Um if e["mig_ds"] <= 60) / max(len(Um), 1)
    b2 = sum(1 for e in U if act_ok(e, e["mig_ds"] + k_s)) / max(len(U), 1)
    b3 = sum(1 for e in U if e.get("first_sell_ds") is not None and e["first_sell_ds"] <= 60) / max(len(U), 1)
    out["feasibility_bar"] = {"n_uniform_migrated": len(Um), "n_uniform_damm_ok": len(U), "b1_comp_to_mig_le60s_share": b1, "b2_active_at_mig_plus_k_share": b2,
                              "b3_other_sell_le60s_share": b3, "adopted": bool(b1 >= 0.9 and b2 >= 0.9 and b3 >= 0.5)}
    return out, per


# --------------------------------------------------------------------------------------------------------------------------------- M-a / M-c from the log
def read_log(root: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    from tools.arena_declog import DecisionLog  # noqa: PLC0415

    lg = DecisionLog(root)
    rows = lg.rows(W.BOOK)
    dec = [r for r in rows if r.get("type") == "decision"]
    ex = {r["key"]: r for r in rows if r.get("type") == "exit"}
    return dec, ex


def md5_keys(keys: list[str]) -> str:
    return W.md5_keys(keys)


def log_measure(out: Path, tp: dict[str, Any], eng: dict[str, Any] | None) -> dict[str, Any]:
    dec, ex = read_log(str(out / "declog"))
    adv = tp["adv"]
    comp = tp["comp"]
    timing = {t["key"]: t["decide_s"] for t in (eng or {}).get("decide_timing", [])}
    recs = []
    for d in dec:
        s, ss, k = int(d["slot"]), float(d["slot_s"]), int(d["k_in"])
        hk = int(float(d["block_time"]) // 3600)
        ss_h = tp["slot_s"].get(hk)
        j = int(np.searchsorted(adv, s, "right"))
        close = int(adv[j]) if j < len(adv) else None
        b = tp["buys"].get(d["pool"], [])
        fol = None
        n_ahead = sum(1 for (bs, _) in b if s < bs <= s + k)
        # trigger signer: the buy print at slot s (the trigger is a BUY print); others = different signer
        trig_signers = {who for (bs, who) in b if bs == s}
        for (bs, who) in b:
            if bs > s and who not in trig_signers:
                fol = bs - s
                break
        e = ex.get(d["key"])
        X = int(e["exit_slot"]) if e else None
        cap_bound = s + max(1, int(round(600.0 / ss))) + S.k_slots(L.LAT_S, ss)
        c = comp.get(d["pool"])
        C = c[0] if c else None
        entry = s + k
        if C is not None and C <= entry:
            cls = "completed_before_entry"
        elif C is not None and X is not None and entry < C <= X:
            cls = "open_at_completion"
        else:
            cls = "not_open_at_completion"
        ub = C is not None and entry < C <= cap_bound
        recs.append({"key": d["key"], "pool": d["pool"], "bt": float(d["block_time"]), "era": "swap3" if float(d["block_time"]) >= SW3_BT else "swap2",
                     "date": S.day_of(float(d["block_time"])), "sw3_pool": d["pool"] in tp["sw3"], "slot_s_live": ss, "slot_s_hour": ss_h, "k_in": k,
                     "close_wait_slots": (close - s) if close is not None else None, "close_wait_s": ((close - s) * (ss_h or ss)) if close is not None else None,
                     "decide_s": timing.get(d["key"]), "follower_dslot": fol, "buys_before_landing": n_ahead, "cls": cls, "upper_bound_open": ub,
                     "comp_after_decision_s": ((c[1] - float(d["block_time"])) if c else None), "exit_slot": X})
    res: dict[str, Any] = {"decisions": len(recs), "exit_rows": len(ex)}
    for era in ("all", "swap2", "swap3"):
        R = [r for r in recs if era == "all" or r["era"] == era]
        days = Counter(r["date"] for r in R)
        res[era] = {"decisions": len(R), "per_day": dict(sorted(days.items())), "cls": dict(Counter(r["cls"] for r in R)),
                    "open_at_completion": sum(1 for r in R if r["cls"] == "open_at_completion"),
                    "open_share": (sum(1 for r in R if r["cls"] == "open_at_completion") / len(R)) if R else None,
                    "upper_bound_open": sum(1 for r in R if r["upper_bound_open"]),
                    "upper_bound_share": (sum(1 for r in R if r["upper_bound_open"]) / len(R)) if R else None,
                    "completed_before_entry": sum(1 for r in R if r["cls"] == "completed_before_entry"),
                    "pools": len({r["pool"] for r in R}), "pools_completing_in_window": len({r["pool"] for r in R if r["pool"] in comp}),
                    "sw3_pool_decisions": sum(1 for r in R if r["sw3_pool"]),
                    "comp_after_decision_s_for_completing": q([r["comp_after_decision_s"] for r in R if r["comp_after_decision_s"] is not None]),
                    "k_in": dict(Counter(r["k_in"] for r in R)), "slot_s_live": q([r["slot_s_live"] for r in R]), "slot_s_hour": q([r["slot_s_hour"] for r in R if r["slot_s_hour"]]),
                    "close_wait_slots": q([r["close_wait_slots"] for r in R if r["close_wait_slots"] is not None]),
                    "close_wait_s": q([r["close_wait_s"] for r in R if r["close_wait_s"] is not None]),
                    "decide_s": q([r["decide_s"] for r in R if r["decide_s"] is not None]),
                    "follower_dslot": q([r["follower_dslot"] for r in R if r["follower_dslot"] is not None]),
                    "follower_none": sum(1 for r in R if r["follower_dslot"] is None),
                    "follower_le_k_share": (sum(1 for r in R if r["follower_dslot"] is not None and r["follower_dslot"] <= r["k_in"]) / len(R)) if R else None,
                    "buys_before_landing": q([r["buys_before_landing"] for r in R])}
    # sanity vs the am5 causal log (Swap2-era window [10-14T00, 10-16T04)): keys decided before 10-16T04
    try:
        refuse_paths(AM5_DECLOG)
        a5, _ = read_log(AM5_DECLOG)
        k5 = [r["key"] for r in a5]
        mine = [r["key"] for r in recs if r["bt"] < SW3_BT]
        res["sanity_am5"] = {"am5_decisions": len(k5), "am5_md5": md5_keys(k5), "mine_before_sw3": len(mine), "mine_md5": md5_keys(mine),
                             "only_mine": len(set(mine) - set(k5)), "only_am5": len(set(k5) - set(mine))}
    except Exception as exc:  # noqa: BLE001
        res["sanity_am5"] = {"error": repr(exc)}
    return res, recs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("engine", "measure", "damm", "all"))
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    out = Path(ns.out)
    refuse_paths(str(out))
    out.mkdir(parents=True, exist_ok=True)
    res: dict[str, Any] = {"schema": "p33_measure_v1", "script_sha256": sha(__file__), "code_sha256": W.code_shas(),
                           "window": [f"{START:%Y-%m-%dT%H}", f"{END:%Y-%m-%dT%H}"], "note": "Counts and decode only. Nothing priced. In-sample exploration hours. No book has cleared the gate."}
    eng = None
    if ns.step in ("engine", "all"):
        eng = run_engine(out)
        print(json.dumps({k: eng[k] for k in ("hours_streamed", "decisions_live", "wall_s", "peak_rss_mib", "declog")}), flush=True)
        if ns.step == "engine":
            return 0
    if eng is None and (out / "engine.json").exists():
        eng = json.loads((out / "engine.json").read_text())
    L.enable_swap3_pricing()
    tp = tape_pass()
    res["tape"] = {"hours": tp["hours"], "incomplete": tp["incomplete"], "row_types": tp["row_types"], "advance_slots": int(len(tp["adv"])),
                   "slot_s_by_hour": {datetime.fromtimestamp(k * 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H"): v for k, v in sorted(tp["slot_s"].items())},
                   "completions": len(tp["comp"]), "sw3_pools": len(tp["sw3"]),
                   "completions_by_era": dict(Counter("swap3" if v[1] >= SW3_BT else "swap2" for v in tp["comp"].values()))}
    dm, per = damm_measure(tp)
    res["damm"] = dm
    with open(out / "damm_per_pool.jsonl", "w") as fh:
        for e in per:
            fh.write(json.dumps(e, default=float) + "\n")
    if ns.step in ("measure", "all") and (out / "declog").exists():
        lm, recs = log_measure(out, tp, eng)
        res["log"] = lm
        if eng:
            res["engine"] = {k: eng[k] for k in ("hours_streamed", "decisions_live", "stats", "wall_s", "peak_rss_mib", "declog", "taken_utc")}
        with open(out / "decisions_measured.jsonl", "w") as fh:
            for r in recs:
                fh.write(json.dumps(r, default=float) + "\n")
    res["taken_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    txt = json.dumps(res, indent=1, default=float)
    (out / "measure.json").write_text(txt)
    print("sha256 measure.json", hashlib.sha256(txt.encode()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
