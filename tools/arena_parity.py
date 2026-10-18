"""Arena parity builder (hunt20). HOST-SIDE: reads ONE exploration tape hour and the hunt8 reference scorer sc.py.

    systemd-run --user --scope -p MemoryMax=12G /data/mal/c1nf-parity-venv/bin/python -m tools.arena_parity build --hour 2026-09-10T12

Writes tools/fixtures/arena_paper/{parity_cases.jsonl, parity_expected.jsonl, PARITY.json}.

PumpSwap path. Reference = /data/mal/hunt-1008/push-1016/hunt8/second-book/h/sc.py (rt / cells, stack 'dec': 505,000 per send, rent 2,039,280, +26.08 / -16 bps)
fed by an INDEPENDENT state extraction written from l3-laya-v1-postgrad/scripts/simlib.py (not tools.r4b_book.mint_states, which the engine uses).
The arena side runs the full engine: rows -> ArenaEngine.on_row, buy / sell calls interleaved in the stream, ledger rows read back.
Bonding path. Reference = tools.latency_curve._state_index / _try_buy / _sell (the lab's curve sim) composed with the stack costs.
Compared to the lamport (and to the bit: the md5 of the float lines is reported next to the md5 of the rounded-lamport lines).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

from tools import r4b_frozen as F
from tools.arena_paper import ArenaEngine, StackConfig, canon, register_bot
from tools.cap_pick_score import DEFAULT_VMAP, load_vmap

HUNT_H = "/data/mal/hunt-1008/push-1016/hunt8/second-book/h"
TAPE = "/data/mal/audit-1008/tape/trades"
FIXDIR = Path(__file__).resolve().parent / "fixtures" / "arena_paper"
SPH = {"2026-09-10": 11395.0}  # slots per hour of the case day (hlib.SPH)
SEED = 20261018
STAKES = (0.25, 0.5, 1.0)
KEYS = ("pnl13", "pnl19", "flat13", "flat19", "press13", "press19")
K_ROUNDING = "ceil"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def md5_lines(lines: list[str]) -> str:
    return hashlib.md5(("\n".join(lines) + "\n").encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------------------- independent extraction (l3 simlib)
class L3Mint:
    """simlib.Mint, one mint's PumpSwap prints (reserves PRE-trade, V added to Q)."""

    def __init__(self, sl, ib, so, tk, q, b, v):
        self.sl = sl
        qL = q[-1] + so[-1] * (1 - 0.0125) if ib[-1] else q[-1] - so[-1] / (1 - 0.0125)
        bL = b[-1] - tk[-1] if ib[-1] else b[-1] + tk[-1]
        self.QX = np.append(q, qL) + np.append(v, v[-1])
        self.BX = np.append(b, bL)
        qp = np.where(ib, q + so * 0.99, q - so * 1.01) + v
        bp = np.where(ib, b - tk, b + tk)
        bp = np.where(bp > 0, bp, b)
        self.pp = qp / bp
        self.cib = np.concatenate([[0], np.cumsum(ib)])
        self.cbv = np.concatenate([[0], np.cumsum(np.where(ib, so, 0))])

    def entry(self, sd: int, lat: float, s_: float):
        sl = self.sl
        k = math.ceil(lat / s_ - 1e-9) if K_ROUNDING == "ceil" else int(round(lat / s_))
        x = sd + k
        je = int(np.searchsorted(sl, x + 1, "left"))
        ix = int(np.searchsorted(sl, x, "left"))
        lo2 = int(np.searchsorted(sl, x - int(round(2.0 / s_)), "left"))
        return self.QX[je], self.BX[je], float(self.cib[je] - self.cib[ix]), float(self.cbv[je] - self.cbv[lo2])

    def spot(self, sd: int) -> float:
        j = int(np.searchsorted(self.sl, sd, "left"))
        return float(self.pp[j - 1])

    def exit_state(self, sx: int, s_: float):
        lag = math.ceil(0.55 / s_ - 1e-9)
        fi = int(np.searchsorted(self.sl, sx + lag + 1, "left"))
        return self.QX[fi], self.BX[fi]


def load_hour(hour: str):
    import duckdb

    c = duckdb.connect()
    c.sql("SET memory_limit='6GB'; SET threads=4; SET enable_progress_bar=false")
    f = f"{TAPE}/{hour}.parquet"
    ps = c.sql(f"select pool, count(*) n from '{f}' where venue='pumpswap' group by 1 having count(*) >= 80").fetchall()
    bd = sorted(c.sql(f"select mint, count(*) n from '{f}' where venue='pump_bonding' group by 1 having count(*) >= 60").fetchall())
    return c, f, ps, bd


def rows_for(c, f, venue: str, keys: list[str]) -> dict[str, list[dict]]:
    col = "pool" if venue == "pumpswap" else "mint"
    lst = ",".join("'" + k + "'" for k in keys)
    cur = c.sql(f"select {col} k, mint, side, sol_lamports, token_raw, quote_reserve, base_reserve, slot, coalesce(tx_index,-1) tx, coalesce(event_index,0) ev, "
                f"block_time bt from '{f}' where venue='{venue}' and {col} in ({lst}) order by {col}, slot, tx, ev").fetchall()
    out: dict[str, list[dict]] = {}
    for k, mint, side, sol, tok, q, b, slot, tx, ev, bt in cur:
        out.setdefault(k, []).append({"venue": venue, "pool": k if venue == "pumpswap" else None, "mint": mint, "side": side, "sol_lamports": int(sol),
                                      "token_raw": float(tok), "quote_reserve": float(q), "base_reserve": float(b), "slot": int(slot), "tx_index": int(tx),
                                      "event_index": int(ev), "event_ts": int(bt) if bt else None,
                                      "signature": f"{len(out.get(k, [])):09d}"})  # file-order ordinal: rows tied on (slot, tx, event) stay distinct and ordered
    return out


def run_stream(engine: ArenaEngine, rows_by_key: dict[str, list[dict]], decisions: list[tuple[int, str, int, str]], bot: str, day_ms: int) -> None:
    """Feed ALL pools' rows as ONE stream in (slot, tx, event) order, so the engine's global slot clock is the live one; call buy / sell right after the
    last row with slot <= the decision slot (what a live bot would have seen). Decisions are (slot, kind, ident, pool)."""
    merged = sorted(((r["slot"], r["tx_index"], r["event_index"], k, i) for k, rows in rows_by_key.items() for i, r in enumerate(rows)))
    ev = sorted(decisions)
    di = 0
    pending: dict[int, str] = {}
    for slot, _, _, k, i in merged:
        while di < len(ev) and ev[di][0] < slot:
            _fire(engine, ev[di], bot, day_ms, pending)
            di += 1
        engine.on_row(rows_by_key[k][i])
    while di < len(ev):
        _fire(engine, ev[di], bot, day_ms, pending)
        di += 1


def _fire(engine, d, bot, day_ms, pending) -> None:
    slot, kind, ident, key = d
    ms = day_ms + slot * 400
    if kind == "buy":
        pending[ident] = engine.buy(bot, key, None, ms, slot, {"case": ident})
    else:
        engine.sell(bot, pending[ident], 1.0, ms, slot, "parity")


# ---------------------------------------------------------------------------------------------------------------- build
def build(hour: str) -> dict:
    sys.path.insert(0, HUNT_H)
    sc = importlib.import_module("sc")
    import pandas as pd

    day = hour[:10]
    s_ = 3600.0 / SPH[day]
    cfg = StackConfig(k_rounding=K_ROUNDING, grace_slots=10**9)  # parity: wait for the tape print the stack uses (END bound), as the sim does
    cfg_live = StackConfig(k_rounding=K_ROUNDING)  # live default: derive the state after the last print once the global clock is grace_slots past
    vmap = load_vmap(DEFAULT_VMAP)
    c, f, ps, bd = load_hour(hour)
    rng = np.random.default_rng(SEED)
    ps = sorted([(p, n) for p, n in ps if vmap.get(p) is not None and 17.5e9 <= vmap[p] <= 17.7e9])
    pick = [ps[i][0] for i in rng.permutation(len(ps))[:75]]
    prow = rows_for(c, f, "pumpswap", pick)
    tmp = tempfile.mkdtemp(prefix="arena_parity_")
    reg = os.path.join(tmp, "bots.json")
    register_bot("parity-bot", "0123abc", 0, reg)
    day_ms = 1_789_000_000_000

    # ---- PumpSwap: decisions
    cases: list[dict] = []
    ps_dec = []  # (key, sd, sx)
    for key in pick:
        rows = prow[key]
        n = len(rows)
        sl = np.array([r["slot"] for r in rows])
        for _ in range(2):
            i = int(rng.integers(5, max(6, n - 40)))
            sd = int(sl[i])
            sx = sd + int(rng.integers(30, 1500))
            need = max(sd + 8, sx + 3) + 1
            if sl[-1] < need or int(np.searchsorted(sl, sd, "left")) == 0:
                continue
            ps_dec.append((key, sd, sx))
    seen_d: set = set()
    ps_dec = [d for d in ps_dec if not ((d[0], d[1]) in seen_d or seen_d.add((d[0], d[1])))][:100]
    ledger_root = os.path.join(tmp, "ledger")
    eng = ArenaEngine(ledger_root, reg, cfg, vmap=vmap, sps_fn=lambda: s_)
    decs = []
    for j, (key, sd, sx) in enumerate(ps_dec):
        decs += [(sd, "buy", j, key), (sx + 11, "sell", j, key)]  # sell decided after the slower leg lands
    ps_dec = [(k, sd, sx + 11) for k, sd, sx in ps_dec]
    used = {k for k, _, _ in ps_dec}
    run_stream(eng, {k: prow[k] for k in used}, decs, "parity-bot", day_ms)
    eng.flush()
    arena_rows = {r["trade_id"]: r for r in eng.ledger.read_bot("parity-bot")}
    eng_l = ArenaEngine(os.path.join(tmp, "ledger_live"), reg, cfg_live, vmap=vmap, sps_fn=lambda: s_)
    run_stream(eng_l, {k: prow[k] for k in used}, decs, "parity-bot", day_ms)
    eng_l.flush()
    live_rows = {r["trade_id"]: r for r in eng_l.ledger.read_bot("parity-bot")}
    live_stats = {"rows": len(live_rows), "derived_last_rows": sum(r["state_src"] == "derived_last" for r in live_rows.values()), "max_abs_diff_pnl_lamports": max(
        abs(live_rows[t]["calc"][g]["pnl_nofail"] - r["calc"][g]["pnl_nofail"]) for t, r in arena_rows.items() for g in ("1p3", "1p9")),
        "rows_with_diff": sum(any(live_rows[t]["calc"][g]["pnl_nofail"] != r["calc"][g]["pnl_nofail"] for g in ("1p3", "1p9")) for t, r in arena_rows.items())}
    mints = {}
    for key in {k for k, _, _ in ps_dec}:
        rows = prow[key]
        a = lambda k_, dt=float: np.array([r[k_] for r in rows], dtype=dt)  # noqa: E731
        mints[key] = L3Mint(np.array([r["slot"] for r in rows]), np.array([r["side"] == "buy" for r in rows]), a("sol_lamports"), a("token_raw"),
                            a("quote_reserve"), a("base_reserve"), np.full(len(rows), float(vmap[key])))
    for j, (key, sd, sx) in enumerate(ps_dec):
        m = mints[key]
        e13, e19 = m.entry(sd, 1.3, s_), m.entry(sd, 1.9, s_)
        qx, bx = m.exit_state(sx, s_)
        for st in STAKES:
            cases.append({"case_id": f"ps{j:03d}-s{int(st * 1000)}", "prog": "pumpswap", "stake_sol": st, "sps": s_, "pool": key, "hour": hour, "slot_entry": sd,
                          "slot_exit": sx, "spot": m.spot(sd), "Qe13": e13[0], "Be13": e13[1], "ssb13": e13[2], "near13": e13[3], "Qe19": e19[0],
                          "Be19": e19[1], "ssb19": e19[2], "near19": e19[3], "Qx": qx, "Bx": bx})

    # ---- PumpSwap reference: sc.py on the panel
    expected: dict[str, dict] = {}
    pcases = [x for x in cases if x["prog"] == "pumpswap"]
    P = pd.DataFrame({"Qe13": [x["Qe13"] for x in pcases], "Be13": [x["Be13"] for x in pcases], "Qe19": [x["Qe19"] for x in pcases],
                      "Be19": [x["Be19"] for x in pcases], "spot": [x["spot"] for x in pcases], "y_p_ssb": [x["ssb13"] for x in pcases],
                      "y_p_nearby": [x["near13"] for x in pcases], "y_b_ssb": [x["ssb19"] for x in pcases], "y_b_nearby": [x["near19"] for x in pcases]})
    for st in STAKES:
        idx = [i for i, x in enumerate(pcases) if x["stake_sol"] == st]
        B = pd.DataFrame({"row": idx, "Qx": [pcases[i]["Qx"] for i in idx], "Bx": [pcases[i]["Bx"] for i in idx]})
        cl = sc.cells(B, P, st * 1e9)
        r13 = sc.rt(B, P, st * 1e9, sc.S505, sc.RENT, "13")
        r19 = sc.rt(B, P, st * 1e9, sc.S505, sc.RENT, "19")
        for n_, i in enumerate(idx):
            expected[pcases[i]["case_id"]] = {"pnl13": float(r13[0][n_]), "pnl19": float(r19[0][n_]), "flat13": float(cl["flat_13"][n_]),
                                              "flat19": float(cl["flat_19"][n_]), "press13": float(cl["press_13"][n_]), "press19": float(cl["press_19"][n_])}

    # ---- arena side for the PumpSwap cases: engine ledger rows (flat from the ledger, press refit per stake book as the scorer does)
    from tools.r4b_book import fail_legs

    got: dict[str, dict] = {}
    for st in STAKES:
        ids = [x["case_id"] for x in pcases if x["stake_sol"] == st]
        rws = [arena_rows[f"{eng_pid(bot='parity-bot', key=x['pool'], sd=x['slot_entry'], day_ms=day_ms)}-s{int(st * 1000)}"] for x in pcases if x["stake_sol"] == st]
        for tag, lat in (("13", "1p3"), ("19", "1p9")):
            pnl = np.array([r["calc"][lat]["pnl_nofail"] for r in rws])
            fil = np.array([r["calc"][lat]["filled"] for r in rws])
            flat, press, _, _ = fail_legs(pnl, fil, np.array([r["calc"][lat]["ssb"] for r in rws]), np.array([r["calc"][lat]["nearby_lamports"] for r in rws]), cfg.send)
            for n_, cid in enumerate(ids):
                g = got.setdefault(cid, {})
                g["pnl" + tag], g["flat" + tag], g["press" + tag] = float(pnl[n_]), float(flat[n_]), float(press[n_])
                led = rws[n_]["legs"][f"flat_{lat}"]["net_lamports"]
                assert led == float(flat[n_]), "ledger flat leg differs from refit flat leg"

    # ---- bonding
    bcases, bgot = bonding_parity(c, f, bd, rng, cfg, vmap, tmp, reg, s_, day_ms)
    cases += bcases
    expected.update({k: v[0] for k, v in bgot.items()})
    got.update({k: v[1] for k, v in bgot.items()})

    # ---- compare + write
    FIXDIR.mkdir(parents=True, exist_ok=True)
    cpath, epath = FIXDIR / "parity_cases.jsonl", FIXDIR / "parity_expected.jsonl"
    cpath.write_text("".join(canon(x) + "\n" for x in cases))
    exp_lines = [canon({"case_id": x["case_id"], **{k: expected[x["case_id"]][k] for k in KEYS}}) for x in cases]
    got_lines = [canon({"case_id": x["case_id"], **{k: got[x["case_id"]][k] for k in KEYS}}) for x in cases]
    epath.write_text("\n".join(exp_lines) + "\n")
    rnd = lambda ls: [canon({k: (round(v) if k != "case_id" else v) for k, v in json.loads(s).items()}) for s in ls]  # noqa: E731
    mism = {"float_exact": 0, "lamport": 0, "max_abs_diff": 0.0}
    by_prog = {"pumpswap": {"cases": 0, "mismatch_lamport": 0}, "pump_bonding": {"cases": 0, "mismatch_lamport": 0}}
    for x in cases:
        e, g = expected[x["case_id"]], got[x["case_id"]]
        bp = by_prog[x["prog"]]
        bp["cases"] += 1
        bad = False
        for k in KEYS:
            d = abs(e[k] - g[k])
            mism["max_abs_diff"] = max(mism["max_abs_diff"], d)
            mism["float_exact"] += e[k] != g[k]
            if round(e[k]) != round(g[k]):
                mism["lamport"] += 1
                bad = True
        bp["mismatch_lamport"] += bad
        if bad and x["prog"] == "pumpswap":
            rr = arena_rows[f"{eng_pid('parity-bot', x['pool'], x['slot_entry'], day_ms)}-s{int(x['stake_sol'] * 1000)}"]
            diag = {t: [x[f"{n}{t[-1]}" if n not in ("Qx", "Bx") else n], rr["calc"][t][m]] for t in ("1p3", "1p9") for n, m in
                    (("Qe", "Qe"), ("Be", "Be"), ("ssb", "ssb"), ("near", "nearby_lamports"), ("Qx", "Qx"), ("Bx", "Bx")) if x[f"{n}{t[-1]}" if n not in ("Qx", "Bx") else n] != rr["calc"][t][m]} if False else {
                f"{n}{t}": [x[f"{n}{t[-2:].replace('p', '')}"] if n not in ("Qx", "Bx") else x[n], rr["calc"][t][m]] for t in ("1p3", "1p9")
                for n, m in (("Qe", "Qe"), ("Be", "Be"), ("ssb", "ssb"), ("near", "nearby_lamports"), ("Qx", "Qx"), ("Bx", "Bx"))
                if (x[f"{n}{t[-2:].replace('p', '')}"] if n not in ("Qx", "Bx") else x[n]) != rr["calc"][t][m]}
            print("MISMATCH", x["case_id"], x["slot_entry"], x["slot_exit"], rr["state_src"], diag, e, g)
    summary = {"hour": hour, "cases": len(cases), "by_prog": by_prog, "legs_compared": len(cases) * len(KEYS), "float_mismatches": int(mism["float_exact"]),
               "lamport_mismatches": int(mism["lamport"]), "max_abs_diff_lamports": mism["max_abs_diff"], "cases_sha256": sha256_file(cpath),
               "expected_md5_float": md5_lines(exp_lines), "arena_md5_float": md5_lines(got_lines), "expected_md5_lamport": md5_lines(rnd(exp_lines)),
               "arena_md5_lamport": md5_lines(rnd(got_lines)), "k_rounding": K_ROUNDING, "sps": s_, "live_derivation_default_grace_25": live_stats}
    (FIXDIR / "PARITY.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def eng_pid(bot: str, key: str, sd: int, day_ms: int) -> str:
    return hashlib.sha256(f"{bot}|{key}|{day_ms + sd * 400}|{sd}".encode()).hexdigest()[:16]


def bonding_parity(c, f, bd, rng, cfg: StackConfig, vmap, tmp, reg, s_, day_ms):
    """Bonding fills: engine (rows -> ledger) vs the lab's curve sim (tools.latency_curve) composed with the stack costs."""
    from tools import latency_curve as lc
    from tools.paper_price_path import TapePrint
    from tools.arena_paper import curve_trip  # noqa: F401  (engine path uses it via ArenaEngine)
    from tools.r4b_book import fail_legs  # noqa: F401
    import hlib

    mints = [m for m, _ in bd]
    pick = [mints[i] for i in rng.permutation(len(mints))[:45]]
    brow = rows_for(c, f, "pump_bonding", pick)
    eng = ArenaEngine(os.path.join(tmp, "ledger_b"), reg, cfg, vmap=vmap, sps_fn=lambda: s_)
    cases, ref_out = [], {}
    dec = []
    for key in pick:
        rows = brow.get(key, [])
        n = len(rows)
        if n < 60:
            continue
        sl = np.array([r["slot"] for r in rows])
        for _ in range(2):
            i = int(rng.integers(5, n - 30))
            sd = int(sl[i])
            sx = sd + int(rng.integers(30, 800)) + 11
            if sl[-1] < sx + 5 or int(np.searchsorted(sl, sd, "left")) == 0:
                continue
            dec.append((key, sd, sx))
    seen_b: set = set()
    dec = [d for d in dec if not ((d[0], d[1]) in seen_b or seen_b.add((d[0], d[1])))][:40]
    decs = []
    for j, (key, sd, sx) in enumerate(dec):
        decs += [(sd, "buy", j, key), (sx, "sell", j, key)]
    run_stream(eng, {k: brow[k] for k in {d[0] for d in dec}}, decs, "parity-bot", day_ms)
    eng.flush()
    arena_rows = {r["trade_id"]: r for r in eng.ledger.read_bot("parity-bot")}
    lag = math.ceil(0.55 / s_ - 1e-9)
    for j, (key, sd, sx) in enumerate(dec):
        rows = brow[key]
        fills = [TapePrint(t_recv_ms=(r["event_ts"] or 0) * 1000, slot=r["slot"], event_index=r["event_index"], venue="pump_bonding", side=r["side"],
                           sol_lamports=r["sol_lamports"], quote_reserve=int(r["quote_reserve"]), base_reserve=int(r["base_reserve"]),
                           price_sol=r["quote_reserve"] / (r["base_reserve"] * 1000.0), market_cap_sol=r["quote_reserve"] / (r["base_reserve"] * 1000.0) * 1e9,
                           tx_index=r["tx_index"]) for r in rows]
        jref = [i for i, fl in enumerate(fills) if fl.slot < sd]
        ref = fills[jref[-1]].price_sol
        legs_in = {}
        for lat, tag in ((1.3, "13"), (1.9, "19")):
            x = sd + (math.ceil(lat / s_ - 1e-9) if K_ROUNDING == "ceil" else int(round(lat / s_)))
            ei = lc._state_index(fills, x, "end")
            xi = lc._state_index(fills, sx + lag, "end")
            ssb = sum(1 for fl in fills if fl.slot == x and fl.side == "buy")
            lo = x - int(round(2.0 / s_))
            near = sum(fl.sol_lamports for fl in fills if fl.side == "buy" and lo <= fl.slot <= x)
            legs_in[tag] = (ei, xi, float(ssb), float(near))
        for st in STAKES:
            size = int(round(st * 1e9))
            cid = f"bc{j:03d}-s{int(st * 1000)}"
            pid = eng_pid("parity-bot", key, sd, day_ms)
            row = arena_rows[f"{pid}-s{int(st * 1000)}"]
            case = {"case_id": cid, "prog": "pump_bonding", "stake_sol": st, "sps": s_, "pool": key, "hour": "", "slot_entry": sd, "slot_exit": sx, "spot": ref}
            exp_leg, got_leg = {}, {}
            for tag in ("13", "19"):
                ei, xi, ssb, near = legs_in[tag]
                s_in, s_out = fills[ei], fills[xi]
                case.update({f"Qe{tag}": float(s_in.quote_reserve), f"Be{tag}": float(s_in.base_reserve), f"ssb{tag}": ssb, f"near{tag}": near})
                case.update({"Qx": float(s_out.quote_reserve), "Bx": float(s_out.base_reserve)})
                buy = lc._try_buy(s_in, size, cfg.portal_ppm, ref)
                if buy is None:
                    pnl, fil = -float(cfg.send), False
                else:
                    tokh = int(buy.tokens_raw / (1 + cfg.h_in))
                    sold = lc._sell(s_out, venue="pump_bonding", tokens=tokh, held=buy.tokens_raw, extra_quote=buy.net_in_lamports, portal_ppm=cfg.portal_ppm)
                    fil = True
                    if sold is None or sold[0] * (1 - cfg.h_out) < cfg.send:
                        pnl = -size - cfg.send - F.RENT_STRANDED
                    else:
                        pnl = sold[0] * (1 - cfg.h_out) - size - 2 * cfg.send - cfg.rent
                exp_leg[tag] = (pnl, fil, ssb, near)
                got_leg[tag] = (row["calc"]["1p3" if tag == "13" else "1p9"]["pnl_nofail"], row["calc"]["1p3" if tag == "13" else "1p9"]["filled"])
            cases.append(case)
            ref_out[cid] = (exp_leg, got_leg)
    # fail legs per (stake, tag) book: expected = hlib (sc.py's copy of the intercept fit), arena = r4b_book.fail_legs
    expected, got = {}, {}
    for st in STAKES:
        ids = [x["case_id"] for x in cases if x["stake_sol"] == st]
        for tag in ("13", "19"):
            pnl_e = np.array([ref_out[i][0][tag][0] for i in ids])
            fil_e = np.array([ref_out[i][0][tag][1] for i in ids])
            ssb = np.array([ref_out[i][0][tag][2] for i in ids])
            near = np.array([ref_out[i][0][tag][3] for i in ids])
            pnl_g = np.array([ref_out[i][1][tag][0] for i in ids])
            fil_g = np.array([ref_out[i][1][tag][1] for i in ids])
            c_, p_ = hlib.press_intercept(ssb, near, fil_e)
            flat_e = np.where(fil_e, 0.85 * pnl_e - 0.15 * cfg.send, pnl_e)
            press_e = np.where(fil_e, (1 - p_) * pnl_e - p_ * cfg.send, pnl_e)
            flat_g, press_g, _, _ = fail_legs(pnl_g, fil_g, ssb, near, cfg.send)
            for n_, i in enumerate(ids):
                e = expected.setdefault(i, ({}, {}))
                e[0].update({"pnl" + tag: float(pnl_e[n_]), "flat" + tag: float(flat_e[n_]), "press" + tag: float(press_e[n_])})
                e[1].update({"pnl" + tag: float(pnl_g[n_]), "flat" + tag: float(flat_g[n_]), "press" + tag: float(press_g[n_])})
    return cases, expected


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--hour", default="2026-09-10T12")
    a = ap.parse_args()
    print(json.dumps(build(a.hour), indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
