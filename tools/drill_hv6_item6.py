#!/usr/bin/env python3
"""HV6 build item 6: md5 decision-equivalence replay + drill (DESIGN section 10; QP comments on #914, #918, #921).

PAPER only. StubModel only: no API call, no key, no RPC. SYNTHETIC tape (tools.test_hv4_runner.write_tape / trade_rows, 2026-09-25, before 2026-10-25T00Z) and
fake chains: no real arena hour is read, so no seal is touched. Every file is written under <HV6_ROOT>/drill/<drill-id>/ (a refused path otherwise); the
production root, bots.json, the real engine ledger and the live chain state are never opened.

It prints canonical-JSON lines of md5 and COUNTS only. It never prints a mint, pool, price, P&L figure or per-pool row. Exit 0 only if all ten drills PASS.
Nothing here is an edge claim: the tape is synthetic.

  1 md5      two independent runs, same code: byte-identical decision-log md5.
  2 leakage  a print stamped after T is refused (LeakageError strict; counted + no card non-strict), control run has cards.
  3 chain    ChainSource + the real ChainPrefetcher (policy v2, 1,000/h) over a fake wall-stamped chain: cards vs refusals by kind.
  4 restart  stop mid-replay, new Runner + new engine + resume(): same md5 as the uninterrupted run; positions == closes + open; each pid closed once.
  5 order    permuted arm order and permuted reply latencies: identical verdicts.
  6 regime   a synthetic block: trigger fires, cap holds (2/day), L and P get the block via make_regime_review_fn(StubModel), L-nr and F get none, a block
             flagged drill/synthetic is refused in the run path.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import dataclasses
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

os.environ["MAL_ARENA_TEST"] = "1"  # the replay engine may lower its caps (its ledger is a drill dir); same switch the unit tests use

from tools import hv4_cards as C  # noqa: E402
from tools import hv6_agents as A  # noqa: E402
from tools import hv6_cards as H  # noqa: E402
from tools import hv6_review as RR  # noqa: E402
from tools import hv6_runner as R  # noqa: E402
from tools.arena_declog import utc_day  # noqa: E402
from tools.arena_paper import ArenaEngine, register_bot  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.hv4_testkit import buy, reply, skip  # noqa: E402

MIN = 60_000
DAY_MS = 86_400_000
FEED_DELAY_SLOTS = 2

# ------------------------------------------------------------------------------------------------------------------------ synthetic tape
# A copy of the builders in tools/test_hv4_runner.py (that module imports pytest, which the job host may lack): two established PumpSwap pools, a pullback path.
SOL = 1_000_000_000
HOUR = 3_600_000
T0 = 1_790_316_000_000  # 2026-09-25T06:00Z, a UTC hour boundary
XM = T0 + 10 * MIN
VQ = 17_584_505_288
WSOL = "So11111111111111111111111111111111111111112"
MINTS = {0: "MintP" + "1" * 39, 1: "MintQ" + "1" * 39}
POOLS = {0: "PxxPA" + "1" * 39, 1: "PxxQA" + "1" * 39}
KNOTS = {0: [(-80, 1.0), (-60, 1.0), (-15, 1.30), (-5, 1.28), (0, 1.18), (5, 1.18), (40, 1.45), (90, 1.45)],
         1: [(-80, 1.0), (-60, 1.0), (-15, 1.30), (-5, 1.28), (0, 1.18), (5, 1.08), (90, 1.0)]}


def _price(k: int, m: float) -> float:
    kn = KNOTS[k]
    for (m0, p0), (m1, p1) in zip(kn, kn[1:]):
        if m0 <= m <= m1:
            return (p0 + (p1 - p0) * (m - m0) / (m1 - m0)) * 1e-6
    return kn[-1][1] * 1e-6


def _hour_name(t: int) -> str:
    from datetime import datetime, timezone

    return "tape-" + datetime.fromtimestamp(t // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def trade_rows(ks=(0, 1)) -> list[dict]:
    rows: list[dict] = []
    n = 0
    for k in ks:
        rows.append({"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 399_000_000, "t_recv_ms": T0 - 3 * HOUR, "signature": f"pc{k}", "event_index": 0,
                     "pool": POOLS[k], "mint": MINTS[k], "quote_mint": WSOL})
        for mi in range(-80, 91):
            for j in range(12):
                n += 1
                t = XM + mi * MIN + j * 5000 + 500 + k * 2000
                p = _price(k, mi + j / 12)
                buy_side = j % 4 != 3
                sol = int((0.15 if buy_side else 0.07) * SOL)
                trader = f"B{k}{n % 10}" if buy_side else f"S{k}{(n // 4) % 4}"
                q, b = 60 * SOL, int((60 * SOL + VQ) / SOL / p * 1_000_000)
                rows.append({"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 400_000_000 + (t - (T0 - 3 * HOUR)) // 400, "t_recv_ms": t,
                             "signature": f"s{n}", "event_index": 0, "pool": POOLS[k], "mint": MINTS[k], "trader": trader, "side": "buy" if buy_side else "sell",
                             "sol_lamports": sol, "token_raw": int(sol / SOL / p * 1_000_000), "quote_reserve": q, "base_reserve": b,
                             "virtual_quote_reserves": VQ, "quote_mint": WSOL, "event_ts": t // 1000})
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


def write_tape(d: Path, rows: list[dict]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(_hour_name(r["t_recv_ms"]), []).append(json.dumps(r))
    for name, lines in by.items():
        (d / name).write_text("\n".join(lines) + "\n")


class FakeChain:
    def __init__(self) -> None:
        self.calls = 0

    def chain_part(self, pool_id, mint, now_ms, slot):
        self.calls += 1
        return {"as_of_ms": now_ms - 1000, "as_of_slot": slot, "mint_authority": None, "freeze_authority": None, "token_program": C.SPL_TOKEN_PROGRAM,
                "extensions": [], "mayhem": False, "top1_frac": 0.05, "top10_frac": 0.30, "holders_as_of_ms": now_ms - 1000}

    def mint_gate(self, mint, now_ms, slot):
        return {"ok": True, "reasons": [], "token": {"token_program": C.SPL_TOKEN_PROGRAM, "extensions": []}}

    def credit_report(self):
        return {"calls": self.calls}

START = T0 + 9 * MIN + 2000
END = T0 + 115 * MIN
MID = T0 + 30 * MIN + 2000
SIM_LIMIT_MS = 1_792_886_400_000  # 2026-10-25T00:00:00Z: no synthetic or real hour at or after it
assert END < SIM_LIMIT_MS


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def out(d: dict) -> None:
    print(json.dumps(d, sort_keys=True, separators=(",", ":")), flush=True)


# ------------------------------------------------------------------------------------------------------------------------ fixtures
class Drill:
    def __init__(self, drill_id: str, sha: str) -> None:
        if not drill_id or "/" in drill_id or ".." in drill_id:
            raise SystemExit("drill: bad --drill-id")
        self.sha = sha
        self.base = Path(H.HV6_ROOT) / "drill" / drill_id
        if not RR.is_drill_root(self.base):
            raise SystemExit("drill: the output dir is not a drill root")
        H.check(self.base / "x", "w")
        self.tape = self.base / "tape"
        write_tape(self.tape, trade_rows())

    def new_run(self, tag: str, *, chain: Any = None, script: Any = None, model_wrap: Any = None, strict: bool = False, fresh_engine: bool = True,
                review: bool = False) -> R.Runner:
        d = self.base / tag
        root = d / "v6"
        guard = A.BudgetGuard(root)
        model = A.StubModel(script or _script, guard=guard, latency_ms=50)
        if model_wrap is not None:
            model = model_wrap(model)
        reg = d / "reg.json"
        eng = ArenaEngine(root / "engine-ledger", reg, cfg=R.stack_config(FEED_DELAY_SLOTS), sps_fn=lambda: 0.4)
        if not reg.exists():
            for a in R.ARM_ORDER:
                register_bot(R.bot_id(a), self.sha[:13], T0 - 60 * MIN, str(reg), deciding_stake=0.25, model_ids=[A.MODEL_ID] if A.ARMS[a].llm else [],
                             now_ms=T0 - 61 * MIN, verify_sha=False)
        cfg = R.RunnerConfig(root=str(root), frozen_sha=self.sha[:13], clock_start_ms=T0 - 60 * MIN, strict_leakage=strict)
        kw = {"review_root": root, "allow_drill_root": True} if review else {}  # versions: the drill root is the ONLY place a drill root is ever allowed
        return R.Runner(cfg, ArenaState(self.tape, start_ms=0), eng, model, chain if chain is not None else FakeChain(), guard, **kw)


def _tagged(req: Any) -> set:
    out_ = set()
    for line in req.user_text.splitlines():
        if line.startswith("{"):
            try:
                c = json.loads(line)
            except ValueError:
                continue
            if isinstance(c, dict) and c.get("setup"):
                out_.add(c["id"])
    return out_


SHORT_PLAN = {"stop_pct": -10.0, "target_pct": 15.0, "trail_pct": 20.0, "time_min": 20}  # short, so positions CLOSE inside the window


def _script(req: Any) -> Any:
    if req.kind == "entry" and req.arm in ("L", "F"):
        return reply(*[buy(i, "BIG", plan=SHORT_PLAN) if i in _tagged(req) else skip(i) for i in req.ids])
    return A.m4.default_reply(req)


def rows(run: R.Runner, name: str, kind: str | None = None) -> list[dict]:
    return [r for r in run.log.rows(name) if kind is None or r["kind"] == kind]


def log_md5(root: str | os.PathLike) -> str:
    """md5 over every decision-log file (arms and SCAN) in sorted name order: the name and the bytes."""
    h = hashlib.md5()
    for f in sorted(Path(root, "decisions").rglob("*.jsonl")):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def cards_md5(root: str | os.PathLike) -> str:
    h = hashlib.md5()
    for f in sorted(Path(root, "cards").rglob("*.jsonl")):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def entries(run: R.Runner) -> dict[str, int]:
    return {a: len(rows(run, a, "entry")) for a in R.ARM_ORDER}


def n_cards(run: R.Runner) -> int:
    return sum(len(p.read_text().splitlines()) for p in Path(run.cfg.root, "cards").glob("*.jsonl"))


def scan_kinds(run: R.Runner) -> dict[str, int]:
    k: dict[str, int] = {}
    for r in rows(run, "SCAN"):
        k[r["kind"]] = k.get(r["kind"], 0) + 1
    return dict(sorted(k.items()))


def refusal_kinds(run: R.Runner) -> dict[str, int]:
    k: dict[str, int] = {}
    for r in rows(run, "SCAN"):
        if r["kind"] == "card_refused":
            for c in r.get("reasons", []):
                k[c] = k.get(c, 0) + 1
        elif r["kind"] == "as_of_refusal":
            k["as_of_refusal"] = k.get("as_of_refusal", 0) + 1
    return dict(sorted(k.items()))


def positions(run: R.Runner) -> dict[str, Any]:
    """Counts of positions vs closes from the logs: each pid has one entry row; a close is a priced sell. Distinct pids closed vs close rows (once each)."""
    refused = {r.get("order_id") for a in R.ARM_ORDER for r in rows(run, a, "order_refused") if r.get("side") == "buy"}
    ent = [r["pos"]["pid"] for a in R.ARM_ORDER for r in rows(run, a, "entry") if r.get("pos") and r["pos"]["pid"] not in refused]
    sells = [r["pid"] for a in R.ARM_ORDER for r in rows(run, a, "sell")]
    closed = {p for p in sells if p in set(ent)}
    openp = sum(len(b.open) for b in run.books.values())
    realized_days = sum(len(b.realized) for b in run.books.values())
    return {"positions": len(ent), "buys_refused_by_engine": len(refused), "distinct_positions": len(set(ent)), "sell_rows": len(sells), "distinct_closed": len(closed), "open_now": openp,
            "realized_day_cells": realized_days}


def realized_md5(run: R.Runner) -> str:
    """md5 of the per-arm per-day realized totals (rounded to 1e-12). The totals themselves are never printed."""
    d = {a: {k: round(v, 12) for k, v in sorted(b.realized.items())} for a, b in run.books.items()}
    return md5_bytes(json.dumps(d, sort_keys=True).encode())


# ------------------------------------------------------------------------------------------------------------------------ drills
def drill_md5(D: Drill) -> dict:
    a = D.new_run("md5-a")
    a.run(START, END)
    b = D.new_run("md5-b")
    b.run(START, END)
    ma, mb = log_md5(a.cfg.root), log_md5(b.cfg.root)
    ca, cb = cards_md5(a.cfg.root), cards_md5(b.cfg.root)
    n_files = len(list(Path(a.cfg.root, "decisions").rglob("*.jsonl")))
    n_rows = sum(len(a.log.rows(x)) for x in (*R.ARM_ORDER, "SCAN"))
    for x in (*R.ARM_ORDER, "SCAN"):
        a.log.verify(x)
        b.log.verify(x)
    ok = ma == mb and ca == cb and entries(a) == entries(b) and n_rows > 0 and entries(a)["X"] > 0 and entries(a)["L"] > 0
    D.ref = a  # the reference run for the restart drill
    D.ref_md5 = ma
    return {"drill": "md5", "result": "PASS" if ok else "FAIL", "run1_log_md5": ma, "run2_log_md5": mb, "run1_cards_md5": ca, "run2_cards_md5": cb,
            "log_files": n_files, "log_rows": n_rows, "entries": entries(a), "cards": n_cards(a), "positions": positions(a)}


def drill_leakage(D: Drill) -> dict:
    win = T0 + 14 * MIN
    ctl = D.new_run("leak-control")
    ctl.run(START, win)
    planted = D.new_run("leak-planted")
    orig = planted._card
    planted_n = [0]

    def card(pool, T, snap, prints, tag, feats):
        planted_n[0] += 1
        return orig(pool, T, snap, list(prints) + [(T + 5000, 1_000_000_000, True, "planted")], tag, feats)  # a print stamped AFTER T

    planted._card = card
    planted.run(START, win)
    strict = D.new_run("leak-strict", strict=True)
    s_orig = strict._card
    strict._card = lambda pool, T, snap, prints, tag, feats: s_orig(pool, T, snap, list(prints) + [(T + 5000, 1, True, "planted")], tag, feats)
    raised = None
    try:
        strict.run(START, win)
    except C.LeakageError:
        raised = "LeakageError"
    unit = None
    try:
        C.assert_as_of({"now_ms": T0}, T0, prints=[(T0, 1, True, "x")])  # a print AT T is already refused (strict t < T)
    except C.LeakageError:
        unit = "LeakageError"
    pc = planted.counters
    ok = (raised == "LeakageError" and unit == "LeakageError" and planted_n[0] > 0 and pc.get("leakage_refused", 0) == planted_n[0]
          and n_cards(planted) == 0 and not any(entries(planted).values()) and n_cards(ctl) > 0 and any(entries(ctl).values())
          and refusal_kinds(planted).get("as_of_refusal", 0) == planted_n[0] and ctl.counters.get("leakage_refused", 0) == 0)
    return {"drill": "leakage", "result": "PASS" if ok else "FAIL", "strict_raised": raised, "unit_print_at_T": unit, "card_attempts_planted": planted_n[0],
            "leakage_refused_planted": pc.get("leakage_refused", 0), "cards_planted": n_cards(planted), "entries_planted": entries(planted),
            "cards_control": n_cards(ctl), "entries_control": entries(ctl), "leakage_refused_control": ctl.counters.get("leakage_refused", 0)}


class WallChain:
    """A fake chain that stamps every fetch with the wall clock `wall`, like HV4Chain. No network."""

    def __init__(self) -> None:
        self.wall, self.fetch_at, self.counter, self.rpc = 0, [], NS(calls=0), NS(provider="helius")
        self.slot_fn = lambda: 0

    def _stamp(self) -> int:
        self.fetch_at.append(self.wall)
        self.counter.calls += 1
        return self.wall

    def mint_facts(self, mint, force=False):
        return NS(ok=True, as_of_ms=self._stamp(), as_of_slot=self.slot_fn(), mint_authority=None, freeze_authority=None, token_program_id=C.SPL_TOKEN_PROGRAM,
                  extensions=(), ext_info={})

    def mayhem_flag(self, mint, pool=None):
        return NS(is_mayhem=False, as_of_ms=self._stamp())

    def largest_holders(self, mint, token_program=None, pool=None, max_age_s=None):
        return NS(ok=True, as_of_ms=self._stamp(), as_of_slot=self.slot_fn(), top1_frac=0.05, top10_frac=0.30)

    def credit_report(self):
        return {"calls": self.counter.calls}


def drill_chain(D: Drill) -> dict:
    from tools.hv4_prefetch import ChainPrefetcher

    wc = WallChain()
    spend = Path(D.base) / "chain" / "v6" / R.CHAIN_SPEND_FILE
    pf = ChainPrefetcher(wc, R.ChainSource.compose, clock=lambda: wc.wall, policy="v2", hourly_budget=R.RPC_CALLS_PER_H, spend_path=H.check(spend, "w"))
    run = D.new_run("chain", chain=R.ChainSource(wc, pf))
    wc.slot_fn = lambda: run.slot
    orig = run.tick
    in_scan_rpc = [0]

    def tick(T):
        if (T // 1000) % 60 == 30:  # the background refresher runs between scans (deterministic: run_once, no thread)
            wc.wall = T
            pf.run_once(T)
        wc.wall = T + 1000  # live: a fetch made inside the tick would be stamped after T
        n = len(wc.fetch_at)
        orig(T)
        in_scan_rpc[0] += len(wc.fetch_at) - n

    run.tick = tick
    run.run(START, END)
    ent, cards, ref = entries(run), n_cards(run), refusal_kinds(run)
    sm = run.summary()
    ok = (ent["L"] > 0 and ent["F"] > 0 and ent["X"] > 0 and ent["L-nr"] == 0 and ent["P"] == 0 and cards > 0 and in_scan_rpc[0] == 0
          and wc.counter.calls <= R.RPC_CALLS_PER_H and all(t < END for t in wc.fetch_at))
    return {"drill": "chain", "result": "PASS" if ok else "FAIL", "path": "ChainSource+ChainPrefetcher(v2,1000/h), fake wall-stamped chain, run_once per scan interval, no thread",
            "cards": cards, "entries": ent, "refusals_by_kind": ref, "counters": dict(sorted(run.counters.items())), "scan_row_kinds": scan_kinds(run),
            "rpc_in_scan_path": in_scan_rpc[0], "fake_chain_calls": wc.counter.calls, "prefetch_stats_keys": sorted(sm["chain_credits"].get("prefetch", {}))}


def drill_restart(D: Drill) -> dict:
    if not hasattr(D, "ref"):
        drill_md5(D)
    ref = D.ref
    r1 = D.new_run("restart")
    r1.run(START, MID)
    open_at_stop = sum(len(b.open) for b in r1.books.values())
    pos_at_stop = positions(r1)["positions"]
    del r1
    r2 = D.new_run("restart")  # new Runner, new ArenaState, new ArenaEngine over the same ledger dir and the same logs
    res = r2.resume()
    r2.run(MID, END)
    m = log_md5(r2.cfg.root)
    p = positions(r2)
    pref = positions(ref)
    ledger_pids = sorted(getattr(r2.engine, "positions", {}))
    ok = (m == D.ref_md5 and realized_md5(r2) == realized_md5(ref) and p == pref and p["positions"] == p["distinct_positions"]
          and p["sell_rows"] == p["distinct_closed"] and p["positions"] == p["distinct_closed"] + p["open_now"] and p["positions"] > 0 and open_at_stop >= 0)
    for x in (*R.ARM_ORDER, "SCAN"):
        r2.log.verify(x)
    return {"drill": "restart", "result": "PASS" if ok else "FAIL", "restart_log_md5": m, "uninterrupted_log_md5": D.ref_md5, "realized_md5_equal": realized_md5(r2) == realized_md5(ref),
            "resume": res, "positions_before_stop": pos_at_stop, "open_at_stop": open_at_stop, "restart_positions": p, "uninterrupted_positions": pref,
            "engine_open_ledger_positions": len(ledger_pids)}


class LatModel:
    def __init__(self, inner: Any, lat: dict) -> None:
        self.inner, self.lat, self.model = inner, lat, inner.model

    def complete(self, req: Any, now_ms: int) -> Any:
        return dataclasses.replace(self.inner.complete(req, now_ms), latency_ms=self.lat[req.arm])


def verdict_md5(run: R.Runner) -> tuple[str, dict]:
    """Verdicts: per LLM arm the ordered (kind, pool, action fields) of entry and skip rows with every timing field dropped."""
    v: dict[str, list] = {}
    for a in run.llm_arm_names:
        seq = []
        for r in run.log.rows(a):
            if r["kind"] in ("entry", "skip"):
                seq.append({k: r[k] for k in sorted(r) if k in ("kind", "pool", "reasons", "size", "conviction", "tag", "plan")})
        v[a] = seq
    return md5_bytes(json.dumps(v, sort_keys=True, default=str).encode()), {a: len(s) for a, s in v.items()}


def drill_order(D: Drill) -> dict:
    arms = ["L", "L-nr", "F", "P"]
    lats = [(1000, 2000, 3000, 4000), (4000, 3000, 2000, 1000), (2000, 4000, 1000, 3000)]
    orders = [tuple(arms), tuple(reversed(arms)), ("F", "P", "L", "L-nr")]
    res = []
    for i, (lat, order) in enumerate(zip(lats, orders)):
        run = D.new_run(f"order-{i}", model_wrap=lambda m, lat=lat: LatModel(m, dict(zip(arms, lat))))
        run.llm_arm_names = order  # the runner's arm iteration order is permuted too
        run.run(START, T0 + 20 * MIN)
        res.append((verdict_md5(run), entries(run)))
    same = len({r[0][0] for r in res}) == 1 and len({json.dumps(r[1], sort_keys=True) for r in res}) == 1
    ok = same and res[0][1]["L"] > 0 and res[0][1]["X"] > 0
    return {"drill": "order", "result": "PASS" if ok else "FAIL", "permutations": len(res), "verdict_md5": [r[0][0] for r in res], "verdict_counts": res[0][0][1],
            "entries": [r[1] for r in res]}


def _blk(ms: int, ids: list[int], **extra: Any) -> dict:
    ev = [{"id": f"RG-{i:08x}", "sev": "HIGH", "relevance": "direct", "kind": "program_upgrade", "program": "pump"} for i in ids]
    return {"schema": "hv6_regime.v1", "as_of_ms": ms, "radar_status": "ok", "events": ev, **extra}


def drill_regime(D: Drill) -> dict:
    seen_by_arm: dict[str, list] = {}

    def script(req):
        seen_by_arm.setdefault(req.arm, []).append("RG-0000" in req.user_text)
        return _script(req)

    run = D.new_run("regime", script=script)
    root = Path(run.cfg.root)
    regime_dir = root / "regime"
    regime_dir.mkdir(parents=True, exist_ok=True)
    commit = D.sha if len(D.sha) == 40 else (D.sha + "0" * 40)[:40]
    genesis_ms = (T0 // DAY_MS - 1) * DAY_MS
    for arm in ("L", "P"):
        row, _ = RR.genesis_manifest(arm, genesis_ms, commit)
        RR.append_manifest(root, arm, row, None, None)
    clk = [T0]
    RR.wall_ms = lambda: clk[0]  # replay: the review runs "now" at the sim instant (drill root only; asserted above)
    assert RR.is_drill_root(root)
    orig_call = RR._call

    def _call(model, req, now_ms):
        clk[0] = now_ms
        return orig_call(model, req, now_ms)

    RR._call = _call
    review_model = A.StubModel(lambda req: "[]")
    fn = RR.make_regime_review_fn(root=root, ledger_root=root / "engine-ledger", model=review_model, commit=commit)
    got: list[tuple[str, bool]] = []

    def hook(arm, blk, T):
        got.append((arm, blk is not None))
        clk[0] = T
        return fn(arm, blk, T)

    run.regime_review_fn = hook

    def put(ms, ids, **extra):
        (regime_dir / f"block-{ms}.json").write_text(json.dumps(_blk(ms, ids, **extra)))

    t = START
    put(t - 1000, [1])
    run.run(t, t + 2 * MIN)                      # trigger 1
    t2 = t + 2 * MIN
    put(t2 - 1000, [1, 2])
    run.run(t2, t2 + 2 * MIN)                    # trigger 2 (new id 2)
    t3 = t2 + 2 * MIN
    put(t3 - 1000, [1, 2, 3])
    run.run(t3, t3 + 2 * MIN)                    # third: capped
    t4 = t3 + 2 * MIN
    put(t4 - 1000, [4], drill=True)
    put(t4 - 900, [5], synthetic=True)
    run.run(t4, t4 + 2 * MIN)                    # flagged blocks: refused, never trigger
    trig = rows(run, "SCAN", "regime_trigger")
    capped = rows(run, "SCAN", "regime_trigger_capped")
    arms_called = sorted({a for a, _ in got})
    manifests = {a: len(RR.read_manifests(root, a)) for a in ("L", "P")}
    no_block_arms = [a for a in ("L-nr", "F") if rows(run, a, "regime_review_pending") or rows(run, a, "regime_review_error")]
    nb_hist = RR.read_manifests  # keep reference quiet for linters
    ok = (len(trig) == 2 and len(capped) >= 1 and arms_called == ["L", "P"] and all(h for _, h in got) and len(got) == 4
          and manifests == {"L": 3, "P": 3} and not no_block_arms and run.regime.refused >= 2 and not ({'RG-00000004', 'RG-00000005'} & run.regime.seen_ids) and not any(seen_by_arm.get("L-nr", [])) and not any(seen_by_arm.get("F", []))
          and not rows(run, "L", "regime_review_error") and not rows(run, "P", "regime_review_error")
          and R.RegimeChannel.block_for("L-nr", {"x": 1}) is None and R.RegimeChannel.block_for("F", {"x": 1}) is None)
    del nb_hist
    return {"drill": "regime", "result": "PASS" if ok else "FAIL", "triggers_fired": len(trig), "triggers_capped": len(capped), "hook_calls": len(got),
            "hook_arms": arms_called, "hook_calls_with_block": sum(1 for _, h in got if h), "manifest_lines_per_arm_incl_genesis": manifests,
            "flagged_block_loads_refused": run.regime.refused, "flagged_ids_seen": len({"RG-00000004", "RG-00000005"} & run.regime.seen_ids), "l_nr_f_logs_with_regime_rows": no_block_arms,
            "l_nr_requests_with_block_text": sum(seen_by_arm.get("L-nr", [])), "f_requests_with_block_text": sum(seen_by_arm.get("F", [])),
            "review_errors": len(rows(run, "L", "regime_review_error")) + len(rows(run, "P", "regime_review_error")),
            "is_drill_root": RR.is_drill_root(root), "stub_model": True}


# ------------------------------------------------------------------------------------------------------------------------ reviewer versions (QP #922 P1)
V_WRITTEN = T0 - 25 * MIN                    # an intraday line written here is in force from + 30 min = T0 + 5 min
V_EFF = V_WRITTEN + 30 * MIN
V_START, V_MID_BEFORE, V_MID_AFTER, V_END = T0 - 10 * MIN + 2000, T0 + 4 * MIN + 2000, T0 + 6 * MIN + 2000, T0 + 30 * MIN
V_BLOCK = {"schema": "hv6_regime.v1", "radar_status": "ok", "as_of_ms": V_WRITTEN - 1000,
           "events": [{"id": "RG-0123abcd", "sev": "HIGH", "relevance": "direct", "kind": "program_upgrade", "program": "pump"}]}
V_TIGHTEN = {"type": "regime_rule", "target": "stop_pct", "new": -10, "ttl_h": 24, "evidence_ids": ["RG-0123abcd"], "why": "pump program redeploy"}
V_PROBE = {"type": "regime_rule", "target": "stop_pct", "new": -30, "ttl_h": 24, "stake": 0.25, "extra_entries_per_h": 1, "evidence_ids": ["RG-0123abcd"],
           "why": "pump program redeploy"}


def seed_versions(D: Drill, run_tag: str, rule: dict) -> None:
    """Genesis lines for L, L-nr, P and one intraday regime line (the rule) for L and P under the run's own drill root. Idempotent (a restart reuses it)."""
    root = D.base / run_tag / "v6"
    commit = D.sha if len(D.sha) == 40 else (D.sha + "0" * 40)[:40]
    cut = RR.RV.day_start_ms(utc_day(T0))
    old = RR.wall_ms
    RR.wall_ms = lambda: V_WRITTEN
    try:
        for arm in ("L", "L-nr", "P"):
            if not RR.read_manifests(root, arm):
                row, _ = RR.genesis_manifest(arm, cut, commit)
                RR.append_manifest(root, arm, row, None, None)
        for arm in ("L", "P"):
            if len(RR.read_manifests(root, arm)) < 2:
                RR.regime_review(arm, utc_day(T0), root=root, ledger_root=None, model=A.StubModel(lambda req: json.dumps([rule])), now_ms=V_WRITTEN, commit=commit,
                                 block=V_BLOCK, records=[])
    finally:
        RR.wall_ms = old


def version_run(D: Drill, tag: str, rule: dict, stop: int | None = None, resume_at: int | None = None) -> R.Runner:
    """One run over the version window; with stop/resume_at: stop at `stop`, build a NEW runner over the same dirs, resume() and go on."""
    seed_versions(D, tag, rule)
    r = D.new_run(tag, review=True)
    if stop is None:
        r.run(V_START, V_END)
        return r
    r.run(V_START, stop)
    del r
    r2 = D.new_run(tag, review=True)
    r2.resume()
    r2.run(stop, V_END)
    return r2


def _versions_ok(run: R.Runner, rule: dict) -> tuple[bool, dict]:
    vl, vp, vn = rows(run, "L", "version"), rows(run, "P", "version"), rows(run, "L-nr", "version")
    ent = rows(run, "L", "entry")
    first_ent_seq = ent[0]["seq"] if ent else None
    ok = ([v["version"] for v in vl] == [0, 1] and [v["version"] for v in vp] == [0, 1] and [v["version"] for v in vn] == [0]
          and vl[1]["ms"] >= V_EFF and vl[1]["ms"] - V_EFF < MIN and bool(ent) and first_ent_seq is not None and vl[1]["seq"] < first_ent_seq
          and all(e["ms"] >= V_EFF for e in ent) and ent[0]["plan"]["stop_pct"] == rule["new"]
          and not any(rows(run, a, "version") for a in ("F", "X")) and run.playbooks["F"].sha256 == A.load_playbook("v0").sha256)
    return ok, {"versions_L": [v["version"] for v in vl], "versions_P": [v["version"] for v in vp], "versions_L_nr": [v["version"] for v in vn],
                "switch_lag_ms": (vl[1]["ms"] - V_EFF) if len(vl) > 1 else None, "version_row_before_first_entry": bool(ent) and vl[1]["seq"] < first_ent_seq,
                "L_entries": len(ent), "F_X_version_rows": sum(len(rows(run, a, "version")) for a in ("F", "X"))}


def drill_version(D: Drill) -> dict:
    a = version_run(D, "ver-a", V_TIGHTEN)
    b = version_run(D, "ver-b", V_TIGHTEN)
    ok, info = _versions_ok(a, V_TIGHTEN)
    ma, mb = log_md5(a.cfg.root), log_md5(b.cfg.root)
    D.ver_ref_md5 = ma
    return {"drill": "version", "result": "PASS" if ok and ma == mb else "FAIL", "run1_log_md5": ma, "run2_log_md5": mb, **info}


def drill_version_restart(D: Drill) -> dict:
    if not hasattr(D, "ver_ref_md5"):
        drill_version(D)
    res, ok = {}, True
    for name, mid in (("before_switch", V_MID_BEFORE), ("after_switch", V_MID_AFTER)):
        r = version_run(D, f"ver-rs-{name}", V_TIGHTEN, stop=mid)
        m = log_md5(r.cfg.root)
        v_ok, _ = _versions_ok(r, V_TIGHTEN)
        for x in (*R.ARM_ORDER, "SCAN"):
            r.log.verify(x)
        res[name] = {"log_md5": m, "equal_to_uninterrupted": m == D.ver_ref_md5, "versions_ok": v_ok, "L_version_rows": len(rows(r, "L", "version"))}
        ok = ok and m == D.ver_ref_md5 and v_ok and len(rows(r, "L", "version")) == 2
    return {"drill": "version_restart", "result": "PASS" if ok else "FAIL", "uninterrupted_log_md5": D.ver_ref_md5, **res}


def drill_probe_restart(D: Drill) -> dict:
    ref = version_run(D, "probe-ref", V_PROBE)
    ref_md5 = log_md5(ref.cfg.root)
    seed_versions(D, "probe-rs", V_PROBE)
    r1 = D.new_run("probe-rs", review=True)
    r1.run(V_START, V_MID_AFTER)
    active_before = r1.probes.active("L", V_MID_AFTER)
    entries_before = len(rows(r1, "L", "entry"))
    del r1
    r2 = D.new_run("probe-rs", review=True)
    r2.resume()
    restored = r2.probes.active("L", V_MID_AFTER) and r2.probes.extra_entries("L", V_MID_AFTER) == 1 and not r2.probes.active("L-nr", V_MID_AFTER)
    r2.run(V_MID_AFTER, V_END)
    ent = rows(r2, "L", "entry")
    m = log_md5(r2.cfg.root)
    ok = (active_before and entries_before == 0 and restored and bool(ent) and all(e["tag"] == "regime_probe" and e["size"] <= A.PROBE_STAKE for e in ent)
          and ent[0]["plan"]["stop_pct"] == V_PROBE["new"] and all(e["tag"] is None for e in rows(r2, "F", "entry")) and m == ref_md5)
    return {"drill": "probe_restart", "result": "PASS" if ok else "FAIL", "probe_active_before_stop": active_before, "L_entries_before_stop": entries_before,
            "probe_restored_after_resume": restored, "L_entries_after": len(ent), "L_entries_tagged_regime_probe": sum(e["tag"] == "regime_probe" for e in ent),
            "max_probe_stake": max((e["size"] for e in ent), default=None), "log_md5": m, "uninterrupted_log_md5": ref_md5, "equal_to_uninterrupted": m == ref_md5}


def drill_halt(D: Drill) -> dict:
    """The window end mid-replay (V2-1): entries halt at a registered instant while positions are open; code exits run to completion; nothing enters; the runner ends by
    itself. Rows before the halt are byte-identical to the uninterrupted reference run."""
    if not hasattr(D, "ref"):
        drill_md5(D)
    ref = D.ref
    first = min(r["ms"] for a in R.ARM_ORDER for r in rows(ref, a, "entry"))
    halt = first + 60_000                      # one minute after the first entry: positions are open
    run = D.new_run("halt")
    run.cfg.entries_halt_ms = halt
    run.run(START, END)
    strip = lambda rs: [{k: v for k, v in r.items() if k not in ("seq", "prev", "sha")} for r in rs]  # noqa: E731
    before_equal = all(strip(r for r in rows(run, x) if r["ms"] < halt) == strip(r for r in rows(ref, x) if r["ms"] < halt) for x in (*R.ARM_ORDER, "SCAN"))
    halt_row = rows(run, "SCAN", "entries_halt")
    entries_after = sum(1 for a in R.ARM_ORDER for r in rows(run, a, "entry") if r["ms"] >= halt)
    llm_after = sum(1 for a in R.ARM_ORDER for r in rows(run, a) if r["ms"] >= halt and r["kind"] in ("llm_call", "watch_add"))
    sells_after = sum(1 for a in R.ARM_ORDER for r in rows(run, a, "sell") if r["ms"] >= halt)
    open_at_end = sum(len(b.open) for b in run.books.values())
    for x in (*R.ARM_ORDER, "SCAN"):
        run.log.verify(x)
    ok = (before_equal and len(halt_row) == 1 and halt_row[0]["open_positions"] > 0 and entries_after == 0 and llm_after == 0 and sells_after > 0 and open_at_end == 0
          and len(rows(run, "SCAN", "run_complete")) == 1 and run._stop)
    return {"drill": "halt", "result": "PASS" if ok else "FAIL", "halt_after_first_entry_ms": 60_000, "rows_before_halt_equal_to_reference": before_equal,
            "open_positions_at_halt": halt_row[0]["open_positions"] if halt_row else None, "entries_after_halt": entries_after, "llm_rows_after_halt": llm_after,
            "exits_after_halt": sells_after, "open_at_end": open_at_end, "run_complete_rows": len(rows(run, "SCAN", "run_complete"))}


# Drill-only equivalence switch for the HV6 fixes A/B/C sha (2026-10-30): puts back, in this process only, the two values fixes A and B changed on purpose
# (entry/re-check max_tokens 400/120, BOARD_SIZE 24: the 8cd8fe54 values), so a drill at the new sha can be compared md5 for md5 with its 8cd8fe54 run.
LEGACY_MAX_TOKENS = {"entry": 400, "recheck": 120}
LEGACY_BOARD_SIZE = 24


@contextmanager
def legacy_constants(on: bool):
    if not on:
        yield
        return
    saved_tokens, saved_board = dict(A.MAX_TOKENS), H.BOARD_SIZE
    A.MAX_TOKENS.update(LEGACY_MAX_TOKENS)
    H.BOARD_SIZE = LEGACY_BOARD_SIZE
    try:
        yield
    finally:
        A.MAX_TOKENS.clear()
        A.MAX_TOKENS.update(saved_tokens)
        H.BOARD_SIZE = saved_board


DRILLS = (("md5", drill_md5), ("leakage", drill_leakage), ("chain", drill_chain), ("restart", drill_restart), ("order", drill_order), ("regime", drill_regime),
          ("version", drill_version), ("version_restart", drill_version_restart), ("probe_restart", drill_probe_restart), ("halt", drill_halt))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV6 item 6 drill: StubModel, synthetic tape, fake chains. Prints md5 and counts only.")
    ap.add_argument("--drill-id", required=True, help="a new directory name under <HV6_ROOT>/drill/")
    ap.add_argument("--sha", required=True, help="the git sha under test (recorded, used as the frozen sha)")
    ap.add_argument("--only", default=None, help="comma list of drills")
    ap.add_argument("--legacy-constants", choices=("on", "off"), default="off", help="drill-only: max_tokens 400/120 and board 24 (the 8cd8fe54 values)")
    a = ap.parse_args(argv)
    only = set(a.only.split(",")) if a.only else None
    legacy = a.legacy_constants == "on"
    D = Drill(a.drill_id, a.sha)
    out({"item6": "start", "sha": a.sha, "tape": "synthetic", "real_arena_hours": 0, "chain": "fake", "model": "StubModel", "api_calls": 0,
         "sim_day": utc_day(T0), "sim_window_min": [(START - T0) // MIN, (END - T0) // MIN], "forbidden_from": "2026-10-25T00:00Z", "legacy_constants": legacy})
    fails = 0
    with legacy_constants(legacy):
        for name, fn in DRILLS:
            if only and name not in only:
                continue
            try:
                r = fn(D)
            except Exception as e:  # noqa: BLE001 - class name only, never the message (it could hold a path or a row)
                r = {"drill": name, "result": "FAIL", "error_class": type(e).__name__}
            fails += r["result"] != "PASS"
            out(r)
    out({"item6": "end", "fails": fails, "result": "PASS" if fails == 0 else "FAIL", "legacy_constants": legacy})
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
