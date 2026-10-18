"""Builds the committed parity set of tools/arena_h5_cost.py (DEC-031 s1.4: the cost layer must match the deciding stack to the lamport on a committed set).

    /data/mal/audit-1008/venv/bin/python -m tools.fixtures.arena_h5.build_parity --out tools/fixtures/arena_h5          (numpy, pandas; exploration tape only)

CASES (parity_cases.jsonl). The shadow engine in fresh-forward configuration is replayed over the exploration tape /data/mal/audit-1008/tape, hours 2026-09-20T00 through
2026-09-21T00 (the md5-proof window; no October data). For every pv trigger with an outcome the scorer's own repricing (tools.arena_h5_score.price_trade) gives the entry
and exit states, the decision spot and the pressure inputs of both entry legs, for the v1 and the v2 exit, at decision ages 0 and 1,200 ms; each leg is a case at the three
stakes 0.10, 0.25, 0.30 SOL. A fixed set of hand-made edge cases is appended (guard, invalid entry, the three invalid-exit causes, a stranded trip, a tiny stake).
EXPECTED (parity_expected.jsonl). The reference is tools/r4b_book.py (round_trip, press_intercept, fail_legs) with the DEC-031 parameters: send 505,000, rent 2,039,280, the
default haircut and guard, auto-stranding and C8 on, then the E1 charge as tools/r4b_read.price_variant does it (the larger of the haircut and 384,022 lamports per 0.05 SOL, linear
in stake). One line per case: pnl (no-fail, after E1), state, flat, and per CELL (stake, latency) the refit intercept, p and press. PARITY.json records the reference files' sha256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np

from tools import arena_h5_cost as cost
from tools import arena_h5_score as sc
from tools import h5_shadow as h5
from tools import r4b_book as bk

REPO = Path(__file__).resolve().parents[3]
HOURS = [f"2026-09-20T{h:02d}" for h in range(24)] + ["2026-09-21T00"]
AGES_MS = (0, 1200)
STAKES = (100_000_000, 250_000_000, 300_000_000)


def replay_records() -> list[dict]:
    rows, meta, sps_pool = h5.replay_rows("/data/mal/audit-1008/tape", h5.WORK_DIR, HOURS)
    recs: list[dict] = []
    eng = h5.Engine(recs.append, boost_mode="auto", sps_fn=lambda p: sps_pool.get(p.pool), classifier=h5.PreEventClassifier(), bx10_enabled=True, fresh_start_ms=0,
                    seal_start_ms=None, h5_look2_start_ms=None)
    h5.replay(rows, meta, eng)
    return recs


def trade_cases(recs: list[dict]) -> list[dict]:
    trig = {r["pool"]: r for r in recs if r["type"] == "trigger" and r["variant"] == "pv"}
    out_ = {r["pool"]: r for r in recs if r["type"] == "outcome" and r["variant"] == "pv"}
    bxs = {r["pool"]: r for r in recs if r["type"] == "outcome_bx10" and r["variant"] == "pv"}
    strips = {r["pool"]: r for r in recs if r["type"] == "strip"}
    cases = []
    for pool in sorted(out_):
        o, t = out_[pool], trig[pool]
        for bot in ("h5v1-boostfloor-a1", "h5v2-boostclock-a1"):
            for age in AGES_MS:
                tt = {**t, "t_detect_ms": int(t["block_time"]) * 1000 + age, "gap": False, "base_breaks_unresolved_settled": 0}
                try:
                    priced = sc.price_trade(bot, {**o, "gap": False}, tt, strips.get(pool), bxs.get(pool), None, {})
                except sc.Excluded:
                    continue
                for shadow_leg, sfx, lat in sc.LEGS:
                    lg = priced["legs"][shadow_leg]
                    if not lg["ok"]:
                        continue
                    for stake in STAKES:
                        cases.append({"id": f"{pool}|{bot[:4]}|{age}|{sfx}|{stake}", "stake": stake, "lat": sfx, "Qe": lg["qe"], "Be": lg["be"], "spot": priced["spot"],
                                      "Qx": lg["qx"], "Bx": lg["bx"], "ssb": lg["ssb"], "nb": lg["nb"]})
    return cases


def edge_cases() -> list[dict]:
    base = dict(stake=250_000_000, lat="1p9", Qe=40e9, Be=2.0e14, spot=40e9 / 2.0e14, Qx=39e9, Bx=2.02e14, ssb=3, nb=2_000_000_000)
    e = []

    def add(name, **kw):
        e.append({**base, "id": f"edge|{name}", **kw})

    add("plain")
    add("guard", spot=base["spot"] / 1.2)  # exec price / spot > 1.15: the buy reverts
    add("entry_invalid_low_q", Qe=17e9)
    add("entry_invalid_base", Be=0.0)
    add("entry_invalid_spot", spot=None)
    add("exit_c2_nan", Qx=None)
    add("exit_c2_low_q", Qx=17e9)
    add("exit_c2_tokens_ge_base", Bx=1e6)
    add("exit_c7_real_quote_bound", Qx=17.6e9, Bx=2.0e14)
    add("exit_c8_k_fall", Qx=30e9, Bx=2.0e14)
    add("stranded_proceeds_below_one_send", stake=1000, Qx=17.5e9 + 1e3, Bx=2.0e14)
    add("tiny_stake", stake=10_000)
    add("big_pressure", ssb=30, nb=40_000_000_000)
    add("no_pressure", ssb=0, nb=0)
    return e


def reference(cases: list[dict]) -> tuple[list[dict], dict]:
    f = lambda x: float("nan") if x is None else float(x)  # noqa: E731
    exp = []
    for c in cases:
        stake = np.array([float(c["stake"])])
        args = (stake, np.array([f(c["Qe"])]), np.array([f(c["Be"])]), np.array([f(c["spot"])]), np.array([f(c["Qx"])]), np.array([f(c["Bx"])]))
        kw = dict(stranded=None, auto_stranded=True, return_stranded=True, return_invalid=True, return_exit_invalid=True, return_exit_cause=True)
        pnl, filled, st, bad, xbad, cause = bk.round_trip(*args, cost.SEND, cost.RENT, **kw)
        if filled[0] and not st[0]:
            pnl0 = bk.round_trip(*args, cost.SEND, cost.RENT, h_in=0.0, h_out=0.0, stranded=None, auto_stranded=True)[0]
            extra = max(0.0, 384_022 * float(c["stake"]) / 50_000_000 - float(pnl0[0] - pnl[0]))
            pnl = pnl - extra
        state = "guarded" if not filled[0] else "entry_invalid" if bad[0] else "exit_invalid" if xbad[0] else "stranded" if st[0] else "filled"
        exp.append({"id": c["id"], "pnl": float(pnl[0]), "state": state, "filled": bool(filled[0]), "cause": int(cause[0]) if state == "exit_invalid" else None})
    cells: dict[str, list[int]] = {}
    for i, c in enumerate(cases):
        cells.setdefault(f"{c['stake']}|{c['lat']}", []).append(i)
    cell_out = {}
    for cell, ix in cells.items():
        pnl = np.array([exp[i]["pnl"] for i in ix])
        filled = np.array([exp[i]["filled"] for i in ix])
        ssb = np.array([float(cases[i]["ssb"]) for i in ix])
        nb = np.array([float(cases[i]["nb"]) for i in ix])
        flat, press, cc, p = bk.fail_legs(pnl, filled, ssb, nb, cost.SEND)
        cell_out[cell] = {"intercept": float(cc), "n": len(ix)}
        for j, i in enumerate(ix):
            exp[i].update({"flat": float(flat[j]), "press": float(press[j]), "p": float(p[j]), "cell": cell})
    return exp, cell_out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    cases = trade_cases(replay_records()) + edge_cases()
    exp, cells = reference(cases)
    out.mkdir(parents=True, exist_ok=True)
    clean = lambda c: {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in c.items()}  # noqa: E731
    (out / "parity_cases.jsonl").write_text("".join(json.dumps(clean(c), allow_nan=False) + "\n" for c in cases))
    (out / "parity_expected.jsonl").write_text("".join(json.dumps(clean(e), allow_nan=False) + "\n" for e in exp))
    states: dict[str, int] = {}
    for e in exp:
        states[e["state"]] = states.get(e["state"], 0) + 1
    git = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (out / "PARITY.json").write_text(json.dumps({
        "reference": {"module": "tools/r4b_book.py", "sha256": sha256(REPO / "tools/r4b_book.py"), "frozen": "tools/r4b_frozen.py", "frozen_sha256": sha256(REPO / "tools/r4b_frozen.py"),
                      "e1": "tools/r4b_read.py price_variant (max(haircut, 384,022 per 0.05 SOL), linear in stake)"},
        "stack": cost.STACK_ID, "params": {"send": cost.SEND, "rent": cost.RENT, "h_in": cost.H_IN, "h_out": cost.H_OUT, "guard": cost.GUARD, "k_tol": cost.K_TOL},
        "tape": "/data/mal/audit-1008/tape", "hours": [HOURS[0], HOURS[-1]], "ages_ms": list(AGES_MS), "stakes": list(STAKES), "built_at_git": git,
        "n_cases": len(cases), "states": states, "cells": cells, "cases_sha256": sha256(out / "parity_cases.jsonl"), "expected_sha256": sha256(out / "parity_expected.jsonl"),
    }, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"n_cases": len(cases), "states": states, "cells": len(cells)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
