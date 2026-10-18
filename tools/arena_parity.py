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

from tools import arena_stack as F
from tools.arena_paper import TEST_ENV, ArenaEngine, StackConfig, canon, register_bot
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


DAY_MS = 1_789_000_000_000
RENT_STRANDED = 2_039_280.0
E1 = 384_022


def rows_for(c, f, venue: str, keys: list[str]) -> dict[str, list[dict]]:
    col = "pool" if venue == "pumpswap" else "mint"
    lst = ",".join("'" + k + "'" for k in keys)
    cur = c.sql(f"select {col} k, mint, side, sol_lamports, token_raw, quote_reserve, base_reserve, slot, coalesce(tx_index,-1) tx, coalesce(event_index,0) ev, "
                f"block_time bt from '{f}' where venue='{venue}' and {col} in ({lst}) order by {col}, slot, tx, ev").fetchall()
    out: dict[str, list[dict]] = {}
    for k, mint, side, sol, tok, q, b, slot, tx, ev, bt in cur:
        out.setdefault(k, []).append({"venue": venue, "pool": k if venue == "pumpswap" else None, "mint": mint, "side": side, "sol_lamports": int(sol),
                                      "token_raw": float(tok), "quote_reserve": float(q), "base_reserve": float(b), "slot": int(slot), "tx_index": int(tx),
                                      "event_index": int(ev), "event_ts": int(bt) if bt else None, "t_recv_ms": DAY_MS + int(slot) * 400,
                                      "signature": f"{len(out.get(k, [])):09d}"})  # file-order ordinal: rows tied on (slot, tx, event) stay distinct and ordered
    return out


def run_stream(engine: ArenaEngine, rows_by_key: dict[str, list[dict]], decisions: list[tuple[int, str, int, str]], bot: str) -> dict[int, str]:
    """Feed ALL pools' rows as ONE stream in (slot, tx, event) order, so the engine's global slot clock is the live one; call buy / sell right after the
    last row with slot <= the decision slot (what a live bot would have seen), at the decision INSTANT that maps back to that slot. Decisions are
    (slot, kind, ident, pool). Returns ident -> position_id."""
    merged = sorted(((r["slot"], r["tx_index"], r["event_index"], k, i) for k, rows in rows_by_key.items() for i, r in enumerate(rows)))
    ev = sorted(decisions)
    di = 0
    pids: dict[int, str] = {}
    for slot, _, _, k, i in merged:
        while di < len(ev) and ev[di][0] < slot:
            _fire(engine, ev[di], bot, pids)
            di += 1
        engine.on_row(rows_by_key[k][i])
    while di < len(ev):
        _fire(engine, ev[di], bot, pids)
        di += 1
    return pids


def _fire(engine, d, bot, pids) -> None:
    slot, kind, ident, key = d
    n = max(0, slot - engine.hw_ms_slot)
    ms = engine.hw_ms + math.ceil(n * engine.sps() * 1000)  # maps back to exactly `slot` (slot_at floors, +1e-9)
    engine.now_fn = lambda m=ms: m  # the call clock is the decision stamp: the write instant is the stamp (replay)
    if kind == "buy":
        pids[ident] = engine.buy(bot, key, None, ms, slot, {"case": ident})
    else:
        engine.sell(bot, pids[ident], 1.0, ms, slot, "parity")


def sc_binding_layer(pnl_sc, pnl0, filled, stake, send, rent):
    """INDEPENDENT reference of the binding layers on top of sc.py's no-fail pnl (numpy, not r4b_book): proceeds below one send is a strand (total loss);
    the E1 floor charges max(haircut, 384,022 per 0.05 SOL) per filled, non-stranded trip; the haircut is sc.py's own pnl with and without H_IN / H_OUT."""
    sv = pnl_sc + stake + 2 * send + rent
    strand = filled & (sv < send)
    extra = np.maximum(0.0, E1 * stake / 5e7 - (pnl0 - pnl_sc))
    pnl = np.where(strand, -stake - send - RENT_STRANDED, np.where(filled, pnl_sc - extra, pnl_sc))
    return pnl, strand


def build(hour: str) -> dict:
    sys.path.insert(0, HUNT_H)
    sc = importlib.import_module("sc")
    import hlib
    import pandas as pd

    from tools.arena_paper import STRAND_STATES, book_fail_legs, price_leg
    from tools.arena_stack import fail_legs

    day = hour[:10]
    s_ = 3600.0 / SPH[day]
    legacy = StackConfig(feed_delay_slots=0, gap_slots=None, gap_silence_ms=None, k_rounding=K_ROUNDING, grace_slots=10**9, strand_rule="sc", e1_floor_per_005=0)  # the sc.py / VERIFY stack; parity waits for the tape print
    binding = StackConfig(feed_delay_slots=0, gap_slots=None, gap_silence_ms=None, k_rounding=K_ROUNDING, grace_slots=10**9, strand_rule="total")  # DEC-031 rev 2: strand class + E1 floor
    live = StackConfig(feed_delay_slots=0, gap_slots=None, gap_silence_ms=None, k_rounding=K_ROUNDING, grace_slots=25, strand_rule="total")  # stress: derive the state after the last print 25 slots past
    vmap = load_vmap(DEFAULT_VMAP)
    c, f, ps, bd = load_hour(hour)
    rng = np.random.default_rng(SEED)
    ps = sorted([(p, n) for p, n in ps if vmap.get(p) is not None and 17.5e9 <= vmap[p] <= 17.7e9])
    pick = [ps[i][0] for i in rng.permutation(len(ps))[:75]]
    prow = rows_for(c, f, "pumpswap", pick)
    tmp = tempfile.mkdtemp(prefix="arena_parity_")
    reg = os.path.join(tmp, "bots.json")
    os.environ[TEST_ENV] = "1"  # the builder is a test harness: fake sha, fake clock
    register_bot("parity-bot", "0123abc", 0, reg, deciding_stake=0.25, model_ids=[], now_ms=0, verify_sha=False)

    def engine(cfg, name):
        return ArenaEngine(os.path.join(tmp, name), reg, cfg, vmap=vmap, sps_fn=lambda: s_, min_free_pct=0.0)  # a temp-dir replay: the production free-space floor does not apply

    # ---- PumpSwap decisions
    cases: list[dict] = []
    ps_dec = []
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
    decs = []
    for j, (key, sd, sx) in enumerate(ps_dec):
        decs += [(sd, "buy", j, key), (sx + 11, "sell", j, key)]  # sell decided after the slower leg lands
    ps_dec = [(k, sd, sx + 11) for k, sd, sx in ps_dec]
    used = {k for k, _, _ in ps_dec}
    runs = {}
    for name, cfg in (("legacy", legacy), ("binding", binding), ("live", live)):
        eng = engine(cfg, "ledger_" + name)
        pids = run_stream(eng, {k: prow[k] for k in used}, decs, "parity-bot")
        eng.flush()
        runs[name] = ({r["trade_id"]: r for r in eng.ledger.read_bot("parity-bot") if r["kind"] == "trade"}, pids, eng)
    live_rows = runs["live"][0]
    bind_rows = runs["binding"][0]
    live_stats = {"note": "grace 25 stress; the default grace is 10,000 slots", "rows": len(live_rows), "derived_last_rows": sum(r["state_src"] == "derived_last" for r in live_rows.values()), "max_abs_diff_pnl_lamports": max(
        abs(live_rows[t]["calc"][g]["by_rule"]["total"]["pnl_nofail"] - r["calc"][g]["by_rule"]["total"]["pnl_nofail"]) for t, r in bind_rows.items() if t in live_rows for g in ("1p3", "1p9")),
        "rows_with_diff": sum(any(live_rows[t]["calc"][g]["by_rule"]["total"]["pnl_nofail"] != r["calc"][g]["by_rule"]["total"]["pnl_nofail"] for g in ("1p3", "1p9")) for t, r in bind_rows.items()
                              if t in live_rows), "excluded_rows": sum(1 for r in runs["live"][2].ledger.read_bot("parity-bot") if r["kind"] == "excluded")}
    mints = {}
    for key in used:
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

    # ---- PumpSwap references: sc.py (legacy) and sc.py + the independent binding layer
    pcases = [x for x in cases if x["prog"] == "pumpswap"]
    P = pd.DataFrame({"Qe13": [x["Qe13"] for x in pcases], "Be13": [x["Be13"] for x in pcases], "Qe19": [x["Qe19"] for x in pcases],
                      "Be19": [x["Be19"] for x in pcases], "spot": [x["spot"] for x in pcases], "y_p_ssb": [x["ssb13"] for x in pcases],
                      "y_p_nearby": [x["near13"] for x in pcases], "y_b_ssb": [x["ssb19"] for x in pcases], "y_b_nearby": [x["near19"] for x in pcases]})
    exp = {"legacy": {}, "binding": {}}
    n_ref_strand = 0
    for st in STAKES:
        idx = [i for i, x in enumerate(pcases) if x["stake_sol"] == st]
        B = pd.DataFrame({"row": idx, "Qx": [pcases[i]["Qx"] for i in idx], "Bx": [pcases[i]["Bx"] for i in idx]})
        cl = sc.cells(B, P, st * 1e9)
        h_in, h_out = sc.H_IN, sc.H_OUT
        for tg in ("13", "19"):
            pnl_sc, fil = sc.rt(B, P, st * 1e9, sc.S505, sc.RENT, tg)
            sc.H_IN = sc.H_OUT = 0.0
            pnl0, _ = sc.rt(B, P, st * 1e9, sc.S505, sc.RENT, tg)
            sc.H_IN, sc.H_OUT = h_in, h_out
            pnl_b, strand = sc_binding_layer(pnl_sc, pnl0, fil, st * 1e9, sc.S505, sc.RENT)
            n_ref_strand += int(strand.sum())
            ssb, near = P["y_p_ssb" if tg == "13" else "y_b_ssb"].values[idx], P["y_p_nearby" if tg == "13" else "y_b_nearby"].values[idx]
            c_, p_ = hlib.press_intercept(ssb, near, fil)
            flat_b = np.where(fil & ~strand, 0.85 * pnl_b - 0.15 * sc.S505, pnl_b)
            press_b = np.where(fil & ~strand, (1 - p_) * pnl_b - p_ * sc.S505, pnl_b)
            for n_, i in enumerate(idx):
                cid = pcases[i]["case_id"]
                exp["legacy"].setdefault(cid, {}).update({"pnl" + tg: float(pnl_sc[n_]), "flat" + tg: float(cl["flat_" + tg][n_]), "press" + tg: float(cl["press_" + tg][n_])})
                exp["binding"].setdefault(cid, {}).update({"pnl" + tg: float(pnl_b[n_]), "flat" + tg: float(flat_b[n_]), "press" + tg: float(press_b[n_])})

    # ---- arena side, PumpSwap
    got = {"legacy": {}, "binding": {}}
    for mode in ("legacy", "binding"):
        rows_m, pids, _ = runs[mode]
        rule = "sc" if mode == "legacy" else "total"
        for st in STAKES:
            sel = [x for x in pcases if x["stake_sol"] == st]
            rws = [rows_m[f"{pids[int(x['case_id'][2:5])]}-s{int(st * 1000)}"] for x in sel]
            for tag, lat in (("13", "1p3"), ("19", "1p9")):
                pnl = np.array([r["calc"][lat]["by_rule"][rule]["pnl_nofail"] for r in rws])
                fil = np.array([r["calc"][lat]["by_rule"][rule]["filled"] for r in rws])
                strand = np.array([r["calc"][lat]["by_rule"][rule]["strand"] for r in rws])
                ssb = np.array([r["calc"][lat]["ssb"] for r in rws])
                near = np.array([r["calc"][lat]["nearby_lamports"] for r in rws])
                flat, press, _, _ = (fail_legs(pnl, fil, ssb, near, legacy.send) if mode == "legacy" else book_fail_legs(pnl, fil, strand, ssb, near, binding.send))
                for n_, x in enumerate(sel):
                    got[mode].setdefault(x["case_id"], {}).update({"pnl" + tag: float(pnl[n_]), "flat" + tag: float(flat[n_]), "press" + tag: float(press[n_])})
                    assert rws[n_]["legs"][rule][f"flat_{lat}"]["net_lamports"] == float(flat[n_]), "ledger flat leg differs from the refit flat leg"

    # ---- states: the engine's extraction equals the independent one (spot, Qe, Be, ssb, nearby, Qx, Bx) on every PumpSwap case
    state_mismatch = []
    rows_m, pids, _ = runs["binding"]
    for x in pcases:
        r = rows_m[f"{pids[int(x['case_id'][2:5])]}-s{int(x['stake_sol'] * 1000)}"]
        for lat in ("1p3", "1p9"):
            tg = lat.replace("p", "")
            for k_, v_ in (("Qe", x["Qe" + tg]), ("Be", x["Be" + tg]), ("ssb", x["ssb" + tg]), ("nearby_lamports", x["near" + tg]), ("Qx", x["Qx"]), ("Bx", x["Bx"]),
                           ("spot", x["spot"])):
                if r["calc"][lat][k_] != v_:
                    state_mismatch.append([x["case_id"], lat, k_])
    # strand classes the arena sees that the sc-based reference (proceeds < send only) does not
    arena_strands = sum(1 for x in pcases for lat in ("1p3", "1p9")
                        if rows_m[f"{pids[int(x['case_id'][2:5])]}-s{int(x['stake_sol'] * 1000)}"]["calc"][lat]["by_rule"]["total"]["strand"])

    # ---- bonding
    bcases, bexp, bgot = bonding_parity(c, f, bd, rng, legacy, binding, vmap, tmp, reg, s_, engine)
    cases += bcases
    for mode in ("legacy", "binding"):
        exp[mode].update(bexp[mode])
        got[mode].update(bgot[mode])

    # ---- compare + write
    FIXDIR.mkdir(parents=True, exist_ok=True)
    cpath = FIXDIR / "parity_cases.jsonl"
    cpath.write_text("".join(canon(x) + "\n" for x in cases))
    summary: dict = {"hour": hour, "cases": len(cases), "legs_compared_per_mode": len(cases) * len(KEYS), "k_rounding": K_ROUNDING, "sps": s_,
                     "cases_sha256": sha256_file(cpath), "derivation_stress_grace_25": live_stats, "pumpswap_state_mismatches": len(state_mismatch),
                     "pumpswap_state_mismatch_detail": state_mismatch[:6],
                     "reference_strand_legs_pumpswap": n_ref_strand, "arena_strand_legs_pumpswap": arena_strands}
    rnd = lambda ls: [canon({k: (round(v) if k != "case_id" else v) for k, v in json.loads(s).items()}) for s in ls]  # noqa: E731
    for mode, fname in (("legacy", "parity_expected.jsonl"), ("binding", "parity_expected_binding.jsonl")):
        exp_lines = [canon({"case_id": x["case_id"], **{k: exp[mode][x["case_id"]][k] for k in KEYS}}) for x in cases]
        got_lines = [canon({"case_id": x["case_id"], **{k: got[mode][x["case_id"]][k] for k in KEYS}}) for x in cases]
        (FIXDIR / fname).write_text("\n".join(exp_lines) + "\n")
        mism = {"float": 0, "lamport": 0, "max_abs_diff": 0.0}
        by_prog = {"pumpswap": {"cases": 0, "mismatch_lamport": 0}, "pump_bonding": {"cases": 0, "mismatch_lamport": 0}}
        for x in cases:
            e, g = exp[mode][x["case_id"]], got[mode][x["case_id"]]
            bp = by_prog[x["prog"]]
            bp["cases"] += 1
            bad = False
            for k in KEYS:
                mism["max_abs_diff"] = max(mism["max_abs_diff"], abs(e[k] - g[k]))
                mism["float"] += e[k] != g[k]
                if round(e[k]) != round(g[k]):
                    mism["lamport"] += 1
                    bad = True
                    print("MISMATCH", mode, x["case_id"], k, e[k], g[k])
            bp["mismatch_lamport"] += bad
        summary[mode] = {"by_prog": by_prog, "float_mismatches": int(mism["float"]), "lamport_mismatches": int(mism["lamport"]),
                         "max_abs_diff_lamports": mism["max_abs_diff"], "expected_md5_float": md5_lines(exp_lines), "arena_md5_float": md5_lines(got_lines),
                         "expected_md5_lamport": md5_lines(rnd(exp_lines)), "arena_md5_lamport": md5_lines(rnd(got_lines))}
    (FIXDIR / "PARITY.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def bonding_parity(c, f, bd, rng, legacy: StackConfig, binding: StackConfig, vmap, tmp, reg, s_, engine):
    """Bonding fills: engine (rows -> ledger) vs the lab's curve sim (tools.latency_curve) composed with the stack costs. The cost layer on top of the lab's
    quotes (haircut on tokens and proceeds, send, rent, strand, E1 floor) is composed HERE, by the same author as the engine (disclosed)."""
    from tools import latency_curve as lc
    from tools.arena_paper import book_fail_legs
    from tools.paper_price_path import TapePrint
    from tools.arena_stack import fail_legs
    import hlib

    mints = [m for m, _ in bd]
    pick = [mints[i] for i in rng.permutation(len(mints))[:45]]
    brow = rows_for(c, f, "pump_bonding", pick)
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
    rows_by_mode, pids_by_mode = {}, {}
    for mode, cfg in (("legacy", legacy), ("binding", binding)):
        eng = engine(cfg, "ledger_b_" + mode)
        pids_by_mode[mode] = run_stream(eng, {k: brow[k] for k in {d[0] for d in dec}}, decs, "parity-bot")
        eng.flush()
        rows_by_mode[mode] = {r["trade_id"]: r for r in eng.ledger.read_bot("parity-bot") if r["kind"] == "trade"}
    lag = math.ceil(0.55 / s_ - 1e-9)
    cases, ref = [], {"legacy": {}, "binding": {}}
    for j, (key, sd, sx) in enumerate(dec):
        rows = brow[key]
        fills = [TapePrint(t_recv_ms=(r["event_ts"] or 0) * 1000, slot=r["slot"], event_index=r["event_index"], venue="pump_bonding", side=r["side"],
                           sol_lamports=r["sol_lamports"], quote_reserve=int(r["quote_reserve"]), base_reserve=int(r["base_reserve"]),
                           price_sol=r["quote_reserve"] / (r["base_reserve"] * 1000.0), market_cap_sol=r["quote_reserve"] / (r["base_reserve"] * 1000.0) * 1e9,
                           tx_index=r["tx_index"]) for r in rows]
        refp = fills[[i for i, fl in enumerate(fills) if fl.slot < sd][-1]].price_sol
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
            case = {"case_id": cid, "prog": "pump_bonding", "stake_sol": st, "sps": s_, "pool": key, "hour": "", "slot_entry": sd, "slot_exit": sx, "spot": refp}
            leg = {"legacy": {}, "binding": {}}
            for tag in ("13", "19"):
                ei, xi, ssb, near = legs_in[tag]
                s_in, s_out = fills[ei], fills[xi]
                case.update({f"Qe{tag}": float(s_in.quote_reserve), f"Be{tag}": float(s_in.base_reserve), f"ssb{tag}": ssb, f"near{tag}": near,
                             "Qx": float(s_out.quote_reserve), "Bx": float(s_out.base_reserve)})
                buy = lc._try_buy(s_in, size, binding.portal_ppm, refp)
                for mode, cfg in (("legacy", legacy), ("binding", binding)):
                    if buy is None:
                        pnl, fil, strand = -float(cfg.send), False, False
                    else:
                        tokh = int(buy.tokens_raw / (1 + cfg.h_in))
                        sold = lc._sell(s_out, venue="pump_bonding", tokens=tokh, held=buy.tokens_raw, extra_quote=buy.net_in_lamports, portal_ppm=cfg.portal_ppm)
                        fil = True
                        dust = mode == "binding" and sold is not None and sold[0] * (1 - cfg.h_out) < cfg.send
                        if sold is None or dust:
                            pnl, strand = -size - cfg.send - RENT_STRANDED, mode == "binding"
                        else:
                            pnl, strand = sold[0] * (1 - cfg.h_out) - size - 2 * cfg.send - cfg.rent, False
                            if cfg.e1_floor_per_005:
                                s0 = lc._sell(s_out, venue="pump_bonding", tokens=buy.tokens_raw, held=buy.tokens_raw, extra_quote=buy.net_in_lamports,
                                              portal_ppm=cfg.portal_ppm)
                                pnl0 = (s0[0] if s0 else 0.0) - size - 2 * cfg.send - cfg.rent
                                pnl -= max(0.0, cfg.e1_floor_per_005 * size / 5e7 - (pnl0 - pnl))
                    leg[mode][tag] = (pnl, fil, strand, ssb, near)
            cases.append(case)
            ref["legacy"][cid], ref["binding"][cid] = leg["legacy"], leg["binding"]
    expected, got = {"legacy": {}, "binding": {}}, {"legacy": {}, "binding": {}}
    for mode, cfg in (("legacy", legacy), ("binding", binding)):
        for st in STAKES:
            ids = [x["case_id"] for x in cases if x["stake_sol"] == st]
            for tag, lat in (("13", "1p3"), ("19", "1p9")):
                pnl_e = np.array([ref[mode][i][tag][0] for i in ids])
                fil_e = np.array([ref[mode][i][tag][1] for i in ids])
                str_e = np.array([ref[mode][i][tag][2] for i in ids])
                ssb = np.array([ref[mode][i][tag][3] for i in ids])
                near = np.array([ref[mode][i][tag][4] for i in ids])
                rws = [rows_by_mode[mode][f"{pids_by_mode[mode][int(i[2:5])]}-s{int(st * 1000)}"] for i in ids]
                rule = "sc" if mode == "legacy" else "total"
                pnl_g = np.array([r["calc"][lat]["by_rule"][rule]["pnl_nofail"] for r in rws])
                fil_g = np.array([r["calc"][lat]["by_rule"][rule]["filled"] for r in rws])
                str_g = np.array([r["calc"][lat]["by_rule"][rule]["strand"] for r in rws])
                c_, p_ = hlib.press_intercept(ssb, near, fil_e)
                flat_e = np.where(fil_e & ~str_e, 0.85 * pnl_e - 0.15 * cfg.send, pnl_e)
                press_e = np.where(fil_e & ~str_e, (1 - p_) * pnl_e - p_ * cfg.send, pnl_e)
                flat_g, press_g, _, _ = fail_legs(pnl_g, fil_g, ssb, near, cfg.send) if mode == "legacy" else book_fail_legs(pnl_g, fil_g, str_g, ssb, near, cfg.send)
                for n_, i in enumerate(ids):
                    expected[mode].setdefault(i, {}).update({"pnl" + tag: float(pnl_e[n_]), "flat" + tag: float(flat_e[n_]), "press" + tag: float(press_e[n_])})
                    got[mode].setdefault(i, {}).update({"pnl" + tag: float(pnl_g[n_]), "flat" + tag: float(flat_g[n_]), "press" + tag: float(press_g[n_])})
    return cases, expected, got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--hour", default="2026-09-10T12")
    a = ap.parse_args()
    print(json.dumps(build(a.hour), indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
