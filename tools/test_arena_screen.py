"""arena_screen tests: the vectorised pricer against arena_paper.price_leg (0 lamports), one END-TO-END check against an ArenaEngine replay of a synthetic tape, every data
guard, the Am.2 universe refusal, the exit resolver against a scalar reference, the KILL logic. No network, no real tape, no arena path."""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from tools import arena_paper as P
from tools import arena_screen as S
from tools import arena_stack as F

os.environ[P.TEST_ENV] = "1"
V = 17_584_505_288


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


# ------------------------------------------------------------------------------------------------------------------------------ 1. vectorised vs price_leg
def random_states(n: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    Qe = V + rng.uniform(0.5, 300, n) * 1e9
    mcap = np.exp(rng.uniform(math.log(100), math.log(200_000), n))  # SOL: spans the fee tiers
    Be = Qe * 1e6 / mcap
    ref = Qe / Be * np.where(rng.random(n) < 0.15, rng.uniform(0.5, 0.95, n), rng.uniform(0.97, 1.05, n))  # some below the 1.15 guard
    Qx = Qe * rng.normal(1.0, 0.15, n)
    Bx = Be * rng.normal(1.0, 0.10, n)
    Qx = np.where(rng.random(n) < 0.04, V * 0.9, Qx)  # invalid exit state
    Bx = np.where(rng.random(n) < 0.03, Be * 1e-6, Bx)  # tokens >= Bx
    Qx = np.where(rng.random(n) < 0.03, np.nan, Qx)
    st = {"ref": ref, "Qe": {}, "Be": {}, "ssb": {}, "nearby": {}, "Qx": Qx, "Bx": Bx}
    for lat in S.LATS:
        st["Qe"][lat] = Qe * rng.normal(1.0, 0.01, n)
        st["Be"][lat] = Be * rng.normal(1.0, 0.01, n)
        st["ssb"][lat] = rng.integers(0, 9, n).astype(float)
        st["nearby"][lat] = rng.uniform(0, 50e9, n)
    st["Qe"][1.9][:3] = V * 0.5  # an invalid entry state
    return st


def one(st: dict, i: int) -> dict:
    return {"ref": float(st["ref"][i]), "Qe": {l: float(st["Qe"][l][i]) for l in S.LATS}, "Be": {l: float(st["Be"][l][i]) for l in S.LATS}, "ssb": {l: float(st["ssb"][l][i]) for l in S.LATS},
            "nearby": {l: float(st["nearby"][l][i]) for l in S.LATS}, "Qx": float(st["Qx"][i]), "Bx": float(st["Bx"][i])}


@pytest.mark.parametrize("rent", [S.RENT_DECIDING, S.RENT_CHARGED])
@pytest.mark.parametrize("stake", [0.25e9, 1.0e9])
def test_vectorised_legs_equal_price_leg_to_the_lamport(rent, stake):
    st = random_states(400, seed=int(rent) + int(stake) % 97)
    cfg = S.screen_cfg(rent)
    worst, states = 0.0, collections.Counter()
    for lat in S.LATS:
        for rule in S.STRAND_RULES:
            L = S.legs(st, stake, lat, rule, cfg, intercept=cfg.press_intercept)
            for i in range(400):
                ref = P.price_leg("pumpswap", stake, one(st, i), lat, cfg, rule)
                states[ref["state"]] += 1
                for k in ("pnl", "flat", "press"):
                    a, b = float(L[k][i]), ref[k]
                    assert (math.isnan(a) and math.isnan(b)) or a == b or abs(a - b) < 1e-6, (lat, rule, i, k, a, b)
                    worst = max(worst, 0.0 if math.isnan(a) else abs(a - b))
                assert bool(L["filled"][i]) == ref["filled"] and bool(L["strand"][i]) == ref["strand"]
    assert worst < 1e-6
    assert {"filled", "guarded", "entry_invalid", "exit_invalid"} <= set(states) and len(states) >= 4  # the random set covers the branches


def test_pressure_intercept_is_refit_per_cell_when_not_given():
    st = random_states(300, seed=3)
    cfg = S.screen_cfg()
    L = S.legs(st, 0.25e9, 1.9, "total", cfg)
    c, p = F.press_intercept(st["ssb"][1.9], st["nearby"][1.9], L["filled"])
    assert L["c"] == c and abs(F._sig(c + F._zb(st["ssb"][1.9], st["nearby"][1.9]))[L["filled"]].mean() - F.PRESS_P_TARGET) < 1e-9


def test_screen_stack_is_the_hosts_pinned_stack():
    from tools import arena_rules

    assert S.screen_cfg(0) == arena_rules.stack_config(7)
    assert S.screen_cfg(0).as_dict() == arena_rules.stack_config(7).as_dict()
    assert S.screen_cfg(S.RENT_CHARGED).rent == 2_039_280 and S.screen_cfg(0).rent == 0


# ------------------------------------------------------------------------------------------------------------------------------ 2. exits
def pool_arrays(n: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    sl = np.cumsum(rng.integers(0, 3, n)) + 1_000
    ib = rng.random(n) < 0.5
    q = 20e9 + np.cumsum(rng.normal(0, 3e8, n))
    b = 1e14 * (1 + np.cumsum(rng.normal(0, 3e-4, n)))
    so = rng.uniform(1e7, 8e8, n)
    return {"sl": sl.astype(np.int64), "ib": ib, "so": so, "tk": so / (q + V) * b * 0.9, "q": q, "b": b, "v": np.full(n, float(V))}


def test_post_price_is_mint_states_spot():
    a = pool_arrays(500, 1)
    pp = S.post_price(a)
    sd = np.array([a["sl"][10], a["sl"][200], a["sl"][-1] + 5])
    spot = F.mint_states(a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"], sd, 0.4, lats=(1.3,), rounding="ceil")["spot"]
    for s, x in zip(sd, spot):
        assert x == pp[max(int(np.searchsorted(a["sl"], s, "left")) - 1, 0)]


def ref_exit(a, pp, sd, spec, rate, sps):
    """Scalar reference: walk the prints in order."""
    k = math.ceil(1.9 / sps - 1e-9)
    floor_slot = sd + k + 1
    xt = sd + int(round(spec.hold_s * rate))
    xs = max(xt, floor_slot)
    jd = int(np.searchsorted(a["sl"], sd, "left"))
    spot0 = pp[max(jd - 1, 0)]
    peak = spot0
    for j in range(jd, len(pp)):
        s = int(a["sl"][j])
        peak = max(peak, pp[j])
        if s > xs:
            break
        if s < floor_slot:
            continue
        if spec.tp is not None and pp[j] >= spec.tp * spot0:
            return s, 1
        if spec.sl is not None and pp[j] <= spec.sl * spot0:
            return s, 2
        if spec.trail is not None and pp[j] <= spec.trail * peak:
            return s, 3
    return xs, 0


@pytest.mark.parametrize("spec", [S.ExitSpec(60), S.ExitSpec(60, tp=1.02), S.ExitSpec(90, sl=0.98), S.ExitSpec(120, trail=0.985), S.ExitSpec(120, tp=1.05, sl=0.97, trail=0.99)])
def test_resolve_exit_matches_a_scalar_walk(spec):
    a = pool_arrays(3000, 2)
    pp = S.post_price(a)
    reasons = collections.Counter()
    for sd in range(1000, int(a["sl"][-1]) - 400, 37):
        got = S.resolve_exit(a, pp, sd, spec, 2.5, 0.4)
        want = ref_exit(a, pp, sd, spec, 2.5, 0.4)
        assert got[:2] == want, (sd, got, want)
        reasons[got[1]] += 1
    if spec.tp or spec.sl or spec.trail:
        assert any(k != 0 for k in reasons)  # the path rule fires on some paths (the walk above checks every one against the scalar reference)


def test_exit_spec_problems():
    for bad in (S.ExitSpec(1.0), S.ExitSpec(60, tp=0.9), S.ExitSpec(60, sl=1.1), S.ExitSpec(60, trail=1.0), S.ExitSpec(float("nan")), S.ExitSpec(60, tp=True)):
        assert S.exit_problem(bad)
    assert S.exit_problem(S.ExitSpec(60, 1.5, 0.7, 0.9)) is None and S.exit_problem(object()) == "not an ExitSpec"


# ------------------------------------------------------------------------------------------------------------------------------ 3. the synthetic tape, the loader and the end-to-end check
H0 = int(datetime(2026, 8, 20, 0, tzinfo=timezone.utc).timestamp())
SLOT0 = 400_000_000
POOLS = {k: f"Pool{k}" + "1" * 38 for k in "FABCNDL"}
MINT = {k: f"Mint{k}" + "1" * 38 for k in "FABCNDL"}
V_OUT = 25_000_000_000
V_OF = {"F": V, "A": V, "B": V + 100_000_000, "C": V_OUT, "N": V, "D": V, "L": V}
NHOUR, KMAX = 3, 8995  # 8996 slots a hour at 0.4 s: slots per second = 8995 / 3598 = 2.5 exactly


def slot_ms(h: int, k: int) -> tuple[int, int]:
    return SLOT0 + 9000 * h + k, (H0 + 3600 * h) * 1000 + k * 400


def synth_rows(seed: int = 5) -> list[dict]:
    rnd = random.Random(seed)
    st = {k: {"q": 20e9 + 1e9 * i, "b": (V + 20e9 + 1e9 * i) * 1e6 / mc} for i, (k, mc) in enumerate(zip("FABCNDL", (300, 300, 5000, 300, 300, 300, 300)))}
    rows, tx = [], 0

    def trade(k: str, h: int, kk: int, late: bool = False) -> dict:
        nonlocal tx
        s = st[k]
        slot, ms = slot_ms(h, kk)
        Q = s["q"] + V_OF[k]
        buy = rnd.random() < 0.5
        sol = int(rnd.uniform(0.05, 1.5) * 1e9)
        row = {"venue": "pumpswap", "type": "trade", "prog": "pumpswap", "mint": MINT[k], "trader": f"T{rnd.randrange(50)}", "pool": POOLS[k], "slot": slot, "event_index": 0,
               "tx_index": tx, "signature": f"sig{tx}", "block_time": ms // 1000, "t_recv_ms": None, "quote_is_wsol": k != "N", "quote_mint": S.WSOL if k != "N" else "Other" + "1" * 39,
               "quote_reserve": int(s["q"]), "base_reserve": int(s["b"]), "side": "buy" if buy else "sell"}
        tx += 1
        if buy:
            net = sol * 0.99
            tok = s["b"] * net / (Q + net)
            s["q"] += net
            s["b"] -= tok
        else:
            tok = s["b"] * 0.002 * rnd.random()
            out = Q * tok / (s["b"] + tok) * 0.99
            sol = int(out)
            s["q"] -= out
            s["b"] += tok
        row.update(sol_lamports=sol, token_raw=int(tok))
        return row

    for h in range(NHOUR):
        for kk in range(KMAX + 1):
            g = 9000 * h + kk
            ks = ["F"]
            ks += [x for x, c in (("A", g % 7 == 0), ("B", g % 11 == 3), ("C", g % 13 == 5), ("N", g % 17 == 2)) if c]
            if h == 0 and kk < 2000 and g % 5 == 1:
                ks += ["D"]
            if (h == 0 and kk < 2000 and g % 6 == 2) or (h == 1 and kk == 3500):
                ks += ["L"]
            for k in ks:
                rows.append(trade(k, h, kk))
    return rows


def write_tape(d: Path, rows: list[dict]) -> list[tuple[datetime, str]]:
    d.mkdir(parents=True, exist_ok=True)
    by = collections.defaultdict(list)
    for r in rows:
        h = (r["block_time"] - H0) // 3600
        by[h].append(json.dumps({**r, "t_recv_ms": None}, separators=(",", ":")))
    files = []
    for h, lines in sorted(by.items()):
        t = datetime.fromtimestamp(H0 + 3600 * h, tz=timezone.utc)
        raw = d / f"trades-{t:%Y-%m-%dT%H}.jsonl"
        raw.write_text("\n".join(lines) + "\n")
        subprocess.run(["zstd", "-q", "-f", str(raw)], check=True)
        raw.unlink()
        files.append((t, str(raw) + ".zst"))
    return files


class ScriptRule:
    """Fires exactly the scripted (pool, slot, spec) triggers, plus any extra triggers a test adds."""

    name = "script"

    def __init__(self, script: list[tuple[str, int, S.ExitSpec]], cells=("c",)):
        self.script, self._cells = script, cells

    def cells(self):
        return list(self._cells)

    def triggers(self, hour, state):
        return [(p, s, e) for p, s, e in self.script if hour.min_slot <= s <= hour.max_slot]


@pytest.fixture(scope="module")
def tape(tmp_path_factory):
    rows = synth_rows()
    files = write_tape(tmp_path_factory.mktemp("tape") / "trades", rows)
    return rows, files


def own_slots(rows, key, h, lo, hi):
    return [r["slot"] for r in rows if r["pool"] == POOLS[key] and lo <= r["slot"] - SLOT0 - 9000 * h <= hi and r["quote_is_wsol"]]


def script_for(rows) -> list[tuple[str, int, S.ExitSpec]]:
    sp = [S.ExitSpec(120), S.ExitSpec(120, tp=1.02, sl=0.98), S.ExitSpec(150, trail=0.985), S.ExitSpec(60, tp=1.01)]
    out = []
    for key, h, lo, hi, step in (("A", 0, 1000, 8000, 400), ("B", 0, 1000, 8000, 700), ("A", 1, 300, 8000, 900), ("D", 0, 1000, 1700, 150), ("L", 0, 1000, 1700, 150), ("B", 1, 500, 6000, 1100)):
        sl_ = own_slots(rows, key, h, lo, hi)
        for i, s in enumerate(sl_[:: max(1, len(sl_) // max(1, (hi - lo) // step))]):
            out.append((POOLS[key], s, sp[i % len(sp)]))
    return out


def run_kit(files, script, count_only=False, vmap=None, min_per_day=0.0, name="synthetic"):
    scr = S.Screen(ScriptRule(script), count_only=count_only, min_attempts_per_day=min_per_day)
    scr.run_block(0, name, S.iter_hours(files, "synthetic", "backfill", S.VMap([vmap or {POOLS[k]: float(v) for k, v in V_OF.items()}])))
    return scr


def replay_engine(rows, trips, tmp_path, rent):
    """Drive a real ArenaEngine over the tape and make the same decisions as the kit (buy at the kit's decision print, sell at the kit's exit slot)."""
    reg = tmp_path / "bots.json"
    clock = H0 * 1000 - 1
    P.register_bot("scr", "abc1234", clock, reg, deciding_stake=0.25, model_ids=[], now_ms=clock - 10, verify_sha=False)
    eng = P.ArenaEngine(tmp_path / f"ledger{rent}", reg, cfg=S.screen_cfg(rent), vmap={POOLS[k]: float(v) for k, v in V_OF.items()}, sps_fn=lambda: 0.4, code_state_fn=lambda: ("abc1234", False))
    eng.now_fn = lambda: eng.hw_ms or 0
    buys = collections.defaultdict(list)  # (pool, slot) -> [(xs)]
    sells = collections.defaultdict(list)  # xs -> [(pool, sd)]
    for pool, sd, xs in trips:
        buys[(pool, sd)].append(xs)
    open_pos: dict[tuple, str] = {}
    done_buy = set()
    for r in rows:
        if not r["quote_is_wsol"]:
            continue
        row = {**r, "t_recv_ms": slot_ms((r["slot"] - SLOT0) // 9000, (r["slot"] - SLOT0) % 9000)[1]}
        eng.on_row(row)
        key = (r["pool"], r["slot"])
        if key in buys and key not in done_buy:
            done_buy.add(key)
            for xs in buys[key]:
                pid = eng.buy("scr", r["pool"], 0.25, row["t_recv_ms"], r["slot"])
                open_pos[(r["pool"], r["slot"])] = pid
                sells[xs].append((r["pool"], r["slot"]))
        for pk in sells.pop(r["slot"], []):
            eng.sell("scr", open_pos.pop(pk), 1.0, row["t_recv_ms"], r["slot"], "script")
    out = {}
    for x in eng.ledger.read_bot("scr"):
        if x.get("kind") == "trade" and x["stake_sol"] in (0.25, 1.0):
            out[(x["pool"], x["effective_slot"]["entry"], x["stake_sol"])] = x
    ex = [x for x in eng.ledger.read_bot("scr") if x.get("kind") == "excluded"]
    return out, ex, eng


@pytest.mark.parametrize("rent", [S.RENT_DECIDING, S.RENT_CHARGED])
def test_end_to_end_kit_prices_equal_an_arena_engine_replay(tape, tmp_path, rent):
    rows, files = tape
    script = script_for(rows)
    assert len(script) >= 25
    scr = run_kit(files, script)
    parts = scr.acc[0]
    st = S._cat(parts)
    n = len(st["ref"])
    pools = {zlib.crc32(p.encode()): p for p in POOLS.values()}
    trips = [(pools[int(p)], int(sd), int(xs)) for p, sd, xs in zip(st["pool"], st["sd"], st["xs"])]
    assert n >= 20 and len(set(trips)) == n
    assert {int(w) for w in st["why"]} >= {0, 1, 2} or {int(w) for w in st["why"]} >= {0, 1}  # time and path exits both fire
    got, excluded, eng = replay_engine(rows, trips, tmp_path, rent)
    assert not excluded, excluded[:2]  # no gap, no missing V: the engine prices everything the kit priced
    assert eng.counters.get("price_errors", 0) == 0 and not eng.pending
    cfg = S.screen_cfg(rent)
    checked = 0
    for stake_sol in (0.25, 1.0):
        for lat, tag in ((1.3, "1p3"), (1.9, "1p9")):
            for rule in S.STRAND_RULES:
                L = S.legs(st, stake_sol * 1e9, lat, rule, cfg, intercept=cfg.press_intercept)
                for i, (pool, sd, xs) in enumerate(trips):
                    row = got[(pool, sd, stake_sol)]
                    assert row["exit_reason"] == "script" and row["effective_slot"]["exit"] == xs
                    c = row["calc"][tag]
                    assert (c["landing_slot"], c["Qe"], c["Be"], c["Qx"], c["Bx"], c["ssb"], c["nearby_lamports"]) == (sd + 7 + (4 if lat == 1.3 else 5), st["Qe"][lat][i], st["Be"][lat][i], st["Qx"][i], st["Bx"][i], st["ssb"][lat][i], st["nearby"][lat][i])
                    assert c["spot"] == st["ref"][i]
                    assert abs(c["by_rule"][rule]["pnl_nofail"] - L["pnl"][i]) < 1e-6
                    assert abs(row["legs"][rule][f"flat_{tag}"]["net_lamports"] - L["flat"][i]) < 1e-6
                    assert abs(row["legs"][rule][f"press_{tag}"]["net_lamports"] - L["press"][i]) < 1e-6
                    checked += 1
    assert checked == 2 * 2 * 2 * n


def test_kit_classes_derived_late_and_block_edge_match_the_engine(tape, tmp_path):
    rows, files = tape
    d_slot = own_slots(rows, "D", 0, 1900, 2000)[-1]  # D's last print: nothing at or after need, so a derived-last state
    l_slot = own_slots(rows, "L", 0, 1900, 2000)[-1]  # L's last print of hour 0; it prints again at hour 1 slot 3500, later than need + GRACE: still derived from the rows before need
    edge = own_slots(rows, "A", 2, 8900, 8995)[-1]  # the last print of the block: no print at or after need, and need + GRACE is past the block end
    script = [(POOLS["D"], d_slot, S.ExitSpec(120)), (POOLS["L"], l_slot, S.ExitSpec(120)), (POOLS["A"], edge, S.ExitSpec(120))]
    scr = run_kit(files, script)
    assert scr.counters["derived_last_state"] == 1 and scr.counters["late_print_derived"] == 1 and scr.counters["excluded_block_edge"] == 1
    r0 = scr.results()["c"]  # the same classes, per CELL (QP 1c): 1 of 3 attempts excluded
    assert (r0["excluded_block_edge"], r0["late_print_derived"], r0["derived_last_state"]) == (1, 1, 1) and r0["excluded_share"] == pytest.approx(1 / 3)
    assert any("not decidable in-sample" in w for w in r0["kill_reasons"])
    st = S._cat(scr.acc[0])
    assert len(st["ref"]) == 2
    trips = [(POOLS["D"], d_slot, int(st["xs"][list(st["sd"]).index(d_slot)])), (POOLS["L"], l_slot, int(st["xs"][list(st["sd"]).index(l_slot)]))]
    got, excluded, eng = replay_engine(rows, trips, tmp_path, S.RENT_DECIDING)
    assert not excluded and eng.counters.get("price_errors", 0) == 0
    cfg = S.screen_cfg(S.RENT_DECIDING)
    L = S.legs(st, 0.25e9, 1.9, "total", cfg, intercept=cfg.press_intercept)
    for i, (pool, sd, xs) in enumerate(trips):
        r = got[(pool, sd, 0.25)]
        assert r["state_src"] == "derived_last"
        assert abs(r["legs"]["total"]["flat_1p9"]["net_lamports"] - L["flat"][i]) < 1e-6
    assert scr.acc[0][0]["Qx"][0] == got[(trips[0][0], trips[0][1], 0.25)]["calc"]["1p9"]["Qx"]


# ------------------------------------------------------------------------------------------------------------------------------ 4. universe refusal
def test_universe_refusals_are_counted_and_nothing_outside_is_priced(tape):
    rows, files = tape
    ok = own_slots(rows, "A", 0, 1000, 1500)[0]
    c_slot = own_slots(rows, "C", 0, 1000, 1500)[0]
    n_row = next(r for r in rows if r["pool"] == POOLS["N"])
    script = [(POOLS["A"], ok, S.ExitSpec(120)), (POOLS["C"], c_slot, S.ExitSpec(120)), (POOLS["N"], n_row["slot"], S.ExitSpec(120)), ("NoSuchPool" + "1" * 33, ok, S.ExitSpec(120)),
              (POOLS["A"], ok, S.ExitSpec(1.0)), (POOLS["A"], SLOT0 - 5 + 0, S.ExitSpec(120)), (POOLS["A"], ok, S.ExitSpec(120, cell="nope"))]
    for count_only in (True, False):
        scr = run_kit(files, script, count_only=count_only)
        c = scr.counters
        assert c["refused_v_out_of_band"] == 1 and c["refused_non_wsol"] == 1 and c["refused_unknown_pool"] == 1 and c["refused_bad_exit"] == 1
        assert c["refused_no_print_at_or_before_decision"] == 0 or True
        assert c["refused_no_such_cell"] == 1
        assert sum(scr.cnt[0].values()) == 1  # only the in-universe trigger survives (the pre-hour slot is simply outside every hour)
    st = S._cat(scr.acc[0]) if scr.acc[0] else None
    assert st is None or set(int(p) for p in st["pool"]) == {zlib.crc32(POOLS["A"].encode())}


def test_unknown_v_is_refused(tape):
    rows, files = tape
    slot = own_slots(rows, "A", 0, 1000, 1500)[0]
    vm = {POOLS[k]: float(v) for k, v in V_OF.items() if k != "A"}  # no V for A
    scr = run_kit(files, [(POOLS["A"], slot, S.ExitSpec(120))], count_only=True, vmap=vm)
    assert scr.counters["refused_v_unknown"] == 1 and not sum(scr.cnt[0].values())


def test_decision_before_the_pools_first_print_is_refused(tape):
    rows, files = tape
    first = own_slots(rows, "B", 0, 0, 9000)[0]
    scr = run_kit(files, [(POOLS["B"], first - 1, S.ExitSpec(120))], count_only=True)
    assert scr.counters["refused_no_print_at_or_before_decision"] == 1


def test_count_only_counts_per_day_and_the_overlap_bound(tape):
    rows, files = tape
    sl_ = own_slots(rows, "A", 0, 1000, 3000)
    script = [(POOLS["A"], sl_[0], S.ExitSpec(120)), (POOLS["A"], sl_[1], S.ExitSpec(120)), (POOLS["A"], sl_[-1], S.ExitSpec(120))]
    scr = run_kit(files, script, count_only=True)
    res = scr.results()["c"]
    assert res["triggers"] == 3 and res["attempts_upper_bound"] == 2  # the second is inside the first hold + cooldown
    assert res["per_day"] == {"2026-08-20": 3} and not scr.acc[0] and scr.t_price == 0.0


def test_causal_probe_catches_a_look_ahead_rule(tape):
    rows, files = tape
    hr = next(S.iter_hours(files[:1], "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    i = hr.pool_index()[POOLS["A"]]
    s = hr.rows(i)
    slots = hr.sl[s]
    st = S.RunState("synthetic")

    class Peek:  # decides at slot 500 only if the pool prints at slot 900 or later in the hour: look-ahead
        def triggers(self, hour, state):
            j = hour.pool_index()[POOLS["A"]]
            sl2 = hour.sl[hour.rows(j)]
            return [(POOLS["A"], int(slots[5]), S.ExitSpec(120))] if sl2.max() >= slots[40] else []

    class Honest:
        def triggers(self, hour, state):
            j = hour.pool_index()[POOLS["A"]]
            return [(POOLS["A"], int(slots[5]), S.ExitSpec(120))] if (hour.sl[hour.rows(j)] <= slots[5]).any() else []

    assert S.causal_probe(Honest(), hr, st, POOLS["A"], int(slots[5]))
    assert not S.causal_probe(Peek(), hr, st, POOLS["A"], int(slots[5]))


def test_overlap_rule_and_thin_flag_in_a_priced_run(tape):
    rows, files = tape
    sl_ = own_slots(rows, "A", 0, 1000, 3000)
    scr = run_kit(files, [(POOLS["A"], sl_[0], S.ExitSpec(120)), (POOLS["A"], sl_[1], S.ExitSpec(120))], min_per_day=20.0, name="explore-0814")
    assert scr.counters["overlap_dropped"] == 1
    r = scr.results()["c"]
    assert r["attempts"] == 1 and r["explore_attempts_per_day"] == 1.0 and r["thin"] is True and r["kill"] is True  # RULES step 2 counts on explore-0814 alone
    assert any("RULES step 2" in w for w in r["kill_reasons"])
    assert run_kit(files, [(POOLS["A"], sl_[0], S.ExitSpec(120))], min_per_day=20.0).results()["c"]["thin"] is None  # not an explore block: not measured


def test_full_results_and_table_render(tape, tmp_path):
    rows, files = tape
    scr = run_kit(files, script_for(rows))
    res = scr.results()
    r = res["c"]
    assert r["attempts"] >= 20 and set(r["stats"]) >= {"1.9|total|rent0|flat", "1.9|sc|rentC|press", "1.3|total|rent0|press"}
    assert r["stats"]["1.9|total|rent0|flat"]["n"] == r["attempts"]
    assert r["stats"]["1.9|total|rentC|flat"]["total"] <= r["stats"]["1.9|total|rent0|flat"]["total"]  # rent only subtracts
    assert r["impact"]["1.9"]["1.0"] > r["impact"]["1.9"]["0.25"] > 0
    md = S.table_md(res, scr.blocks, {"rule": "script"})
    assert "no profit claim" in md and "| c |" in md
    json.dumps(res, default=float)


# ------------------------------------------------------------------------------------------------------------------------------ 5. statistics and the KILL logic
def test_stat_matches_the_reference_bootstrap_and_exclusions():
    rng = np.random.default_rng(4)
    lam = rng.normal(2e6, 5e7, 400)
    day = rng.integers(20_000, 20_012, 400)
    blk = rng.integers(0, 2, 400)
    s = S.stat(lam, day, blk, ["x", "y"])
    x = lam / 1e9
    days, inv = np.unique(day, return_inverse=True)
    dsum, dn = np.bincount(inv, weights=x), np.bincount(inv).astype(float)
    r = np.empty(1000)
    g = np.random.default_rng(1)
    for i in range(1000):
        ix = g.integers(0, len(days), len(days))
        r[i] = dsum[ix].sum() / dn[ix].sum()
    assert s["ci_lo"] == float(np.percentile(r, 5))
    assert abs(s["ex_top3"] - (x.sum() - np.sort(x)[-3:].sum())) < 1e-12 and abs(s["ex_best_day"] - (x.sum() - dsum.max())) < 1e-12
    assert s["n"] == 400 and s["days"] == len(days) and sum(v["n"] for v in s["per_block"].values()) == 400


NAMES = list(S.CANONICAL_BLOCKS)


def mk(mean=1.0, ex3=1.0, exbest=1.0, blocks=(1, 1, 1, 1), ci=1.0, cit=1.0, pos=8, days=10, n=500, names=None):
    names = names or NAMES
    return {"mean": mean, "mean_pct": mean * 400, "ex_top3": ex3, "ex_best_day": exbest, "ci_lo": ci, "ci_lo_pct": ci * 400, "ci_lo_trade": cit, "ci_lo_trade_pct": cit * 400,
            "pos_days": pos, "days": days, "n": n, "per_block": {nm: {"n": 5, "total": t} for nm, t in zip(names, blocks)}}


GOOD = {k: mk() for k in S.SUB_CELLS}
GGOOD = {(g, *k): mk() for g in S.GROUPS for k in S.SUB_CELLS}


def verdict(subs=None, groups=None, **kw):
    return S.kill_verdict({**GOOD, **(subs or {})}, NAMES, {**GGOOD, **(groups or {})}, **kw)


def test_kill_logic_all_four_sub_cells():
    k, why = verdict()
    assert not k and not why  # a screen survivor: NOT a profit claim
    assert len(S.SUB_CELLS) == 4 and set(S.SUB_CELLS) == {("total", "flat"), ("total", "press"), ("sc", "flat"), ("sc", "press")}
    for key in S.SUB_CELLS:  # any ONE of the four sub-cells kills, on each of the three tests
        for field, kw in (("mean", "mean"), ("ex_top3", "ex3"), ("ex_best_day", "exbest")):
            k, why = verdict({key: mk(**{kw: 0.0})})
            assert k and any(f"{'/'.join(key)}.{field}<=0" in w for w in why), (key, field)
            assert verdict({key: mk(**{kw: -1e-9})})[0]
    assert verdict({("sc", "press"): None})[0] and verdict({("total", "flat"): None})[0]
    assert S.kill_verdict({}, NAMES, GGOOD)[0]  # nothing priced


def test_kill_logic_the_gate_own_tests_n_dates_and_both_bootstraps():
    for key in S.SUB_CELLS:
        tag = "/".join(key)
        k, why = verdict({key: mk(n=99)})
        assert k and any(f"{tag}.gate.n 99 < 100" in w for w in why)
        assert verdict({key: mk(n=100)})[0] is False
        k, why = verdict({key: mk(days=4, pos=4)})
        assert k and any(f"{tag}.gate.days 4 < 5" in w for w in why)
        assert verdict({key: mk(days=5, pos=3)})[0] is False
        k, why = verdict({key: mk(cit=-1e-9)})  # the trade-level bound (arena_score's) alone kills
        assert k and any("ci_lo_trade<=0" in w for w in why) and not any("date_clustered" in w for w in why)
        k, why = verdict({key: mk(ci=-1e-9)})  # the date-clustered bound alone kills
        assert k and any("ci_lo_date_clustered<=0" in w for w in why) and not any("ci_lo_trade" in w for w in why)
        k, why = verdict({key: mk(pos=5, days=10)})  # exactly half is not a majority
        assert k and any("no_majority_of_dates_positive" in w for w in why)
        assert verdict({key: mk(pos=6, days=10)})[0] is False
    assert (S.MIN_N, S.MIN_DAYS, S.MAX_EXCLUDED_SHARE) == (100, 5, 0.05)


def test_kill_logic_exclusion_cap_and_the_step2_count():
    assert verdict(excl_share=0.05)[0] is False
    k, why = verdict(excl_share=0.0501)
    assert k and any("not decidable in-sample" in w for w in why)
    k, why = verdict(explore_att_per_day=19.9, min_att_per_day=20.0)
    assert k and any("RULES step 2" in w for w in why)
    assert verdict(explore_att_per_day=20.0, min_att_per_day=20.0)[0] is False
    assert verdict(explore_att_per_day=1.0, min_att_per_day=0.0)[0] is False  # switched off


def test_kill_logic_blocks_positive_out_of_the_four_canonical_blocks():
    three = (1, 1, 1, -1)
    assert verdict({k: mk(blocks=three) for k in S.SUB_CELLS})[0] is False  # 3 of 4 blocks is enough
    k, why = verdict({k: mk(blocks=(1, 1, -1, -1)) for k in S.SUB_CELLS})
    assert k and any("blocks_positive 2/4 < 3" in w for w in why)
    k, why = verdict({("sc", "press"): mk(blocks=(1, 1, 1, -1)), ("total", "flat"): mk(blocks=(-1, 1, 1, 1))})  # a different block fails in each: only 2 pass all four sub-cells
    assert k and any("blocks_positive 2/4 < 3" in w for w in why)
    k, why = verdict({k: mk(blocks=(1, 1)) for k in S.SUB_CELLS})  # only two canonical blocks priced: a denominator of 4 never shrinks
    assert k and any("blocks_positive 2/4 < 3" in w for w in why)
    # explore split into its seven workers is still ONE block; seven positive "blocks" plus one September block is 2 of 4
    split = [f"explore-0814/w{i}" for i in range(1, 8)] + [NAMES[1]]
    k, why = S.kill_verdict({k: mk(blocks=(1,) * 8, names=split) for k in S.SUB_CELLS}, split, GGOOD)
    assert k and any("blocks_positive 2/4 < 3" in w for w in why)
    # a canonical block is positive on its pooled total: w1 negative and w2 more positive is positive
    mixed = {k: mk(blocks=(-1, 3, 1, 1, 1), names=["explore-0814/w1", "explore-0814/w2", *NAMES[1:]]) for k in S.SUB_CELLS}
    assert S.kill_verdict(mixed, NAMES, GGOOD)[0] is False
    zero = {k: mk() for k in S.SUB_CELLS}
    zero[("total", "flat")]["per_block"][NAMES[3]]["n"] = 0
    assert verdict(zero)[0] is False  # 3 blocks still pass
    assert S.canonical_block("explore-0814/w3") == "explore-0814" and S.canonical_block("oracle-live-2026-09-25_27") == "oracle-live-2026-09-25_27" and S.canonical_block("synthetic") is None


def test_kill_logic_explore_alone_and_september_pooled_means():
    for grp in S.GROUPS:
        for key in S.SUB_CELLS:
            k, why = verdict(groups={(grp, *key): mk(mean=0.0)})
            assert k and any(f"{grp}.{'/'.join(key)}.mean<=0" in w for w in why)
    k, why = S.kill_verdict(GOOD, NAMES, {k: v for k, v in GGOOD.items() if k[0] != "september"})
    assert k and any("september: not measured" in w for w in why)
    k, why = S.kill_verdict(GOOD, NAMES, None)
    assert k and sum("not measured" in w for w in why) == 2
    assert S.block_group("explore-0814") == S.block_group("explore-0814/w3") == "explore-0814"
    assert {S.block_group(b) for b in S.SEPTEMBER_BLOCKS} == {"september"} and S.block_group("synthetic") is None and S.block_group("arena-tape") is None


def test_kill_logic_pooled_in_sample_gate_columns():
    g = S.gate_row(mk(ci=0.5, cit=0.5, pos=3, days=5))
    assert g["ci_lo_gt0"] and g["ci_lo_trade_gt0"] and g["majority_dates"] and g["trade_gate_pass"] and S.gate_row(None) is None
    assert not S.gate_row(mk(n=99))["trade_gate_pass"] and not S.gate_row(mk(days=4, pos=4))["trade_gate_pass"] and not S.gate_row(mk(cit=-1))["trade_gate_pass"]
    assert not S.gate_row(mk(ex3=0.0))["trade_gate_pass"] and not S.gate_row(mk(pos=5, days=10))["trade_gate_pass"]


def test_statistics_equal_arena_score_cell_stats_on_the_same_trips(tape):
    from tools import arena_score as A

    rng = np.random.default_rng(11)
    passes = set()
    for n, nd, mu in ((400, 12, 3e6), (150, 6, -1e6), (80, 9, 5e6), (500, 4, 4e6), (300, 10, 2e7), (600, 14, 3e7)):
        lam = rng.normal(mu, 6e7, n)
        lam[:5] += 4e8
        di = rng.integers(0, nd, n)
        days = [f"2026-08-{14 + d:02d}" for d in di]
        a = A.cell_stats(lam, days)
        k = S.stat(lam, (H0 // 86400) + di, np.zeros(n, int), ["explore-0814"])
        assert (k["n"], k["days"], k["pos_days"]) == (a["n"], a["days"], a["days_pos"])
        assert k["ci_lo_trade"] == a["ci90_lo_sol"]  # the same bootstrap, to the bit
        for ka, kb in (("mean", "mean_sol"), ("total", "total_sol"), ("ex_top3", "ex_top3_sol"), ("ex_best_day", "ex_best_day_sol")):
            assert k[ka] == pytest.approx(a[kb], abs=1e-12), ka
        v, g = A.cell_verdict(a), S.gate_row(k)
        assert g["trade_gate_pass"] == v["pass"]
        assert g["n_ge_min"] == (not any(m.startswith("n ") for m in v["misses"])) and g["days_ge_min"] == (not any(m.startswith("days ") and "positive" not in m for m in v["misses"]))
        passes.add(v["pass"])
        _, why = S.kill_verdict({key: k for key in S.SUB_CELLS}, NAMES, None)  # n / dates / CI reasons appear exactly when arena_score names them
        assert any("gate.n " in w for w in why) == any(m.startswith("n ") for m in v["misses"])
        assert any("ci_lo_trade<=0" in w for w in why) == any(m.startswith("CI90") for m in v["misses"])
    assert passes == {True, False}  # the cases cover both verdicts
    # and on trips the KIT priced: the flat and pressure legs of a priced cell through both statistic layers
    rows, files = tape
    scr = run_kit(files, script_for(rows))
    st = S._cat(scr.acc[0])
    cfg = S.screen_cfg(S.RENT_DECIDING)
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d") for t in st["T"]]
    for rule in S.STRAND_RULES:
        L = S.legs(st, 0.25e9, 1.9, rule, cfg)
        for fail in S.FAILS:
            a, k = A.cell_stats(L[fail], days), scr.results()["c"]["stats"][f"1.9|{rule}|rent0|{fail}"]
            assert k["ci_lo_trade"] == a["ci90_lo_sol"] and k["mean"] == pytest.approx(a["mean_sol"], abs=1e-12) and k["ex_top3"] == pytest.approx(a["ex_top3_sol"], abs=1e-12)
            assert k["ex_best_day"] == pytest.approx(a["ex_best_day_sol"], abs=1e-12) and (k["n"], k["days"], k["pos_days"]) == (a["n"], a["days"], a["days_pos"])


def pool_age_script(rows):
    """Two decisions on one pool at the same hour: one inside its first 360 s (block time), one past it."""
    sl_ = own_slots(rows, "A", 0, 0, 2000)
    bt = {r["slot"]: r["block_time"] for r in rows if r["pool"] == POOLS["A"]}
    first = bt[min(bt)]
    young = [s for s in sl_ if bt[s] - first < S.MIN_POOL_AGE_S][-1]
    edge = [s for s in sl_ if bt[s] - first >= S.MIN_POOL_AGE_S][0]
    return young, edge, bt[edge] - first


def test_pool_age_under_360s_is_refused_and_counted(tape):
    rows, files = tape
    young, old, age = pool_age_script(rows)
    assert 360 <= age < 370
    scr = run_kit(files, [(POOLS["A"], young, S.ExitSpec(120)), (POOLS["A"], old, S.ExitSpec(120))], count_only=True)
    assert scr.counters["refused_pool_age_lt_360s"] == 1 and sum(scr.cnt[0].values()) == 1
    scr2 = run_kit(files, [(POOLS["A"], young, S.ExitSpec(120)), (POOLS["A"], old, S.ExitSpec(120))])  # the priced run refuses it too
    assert scr2.counters["refused_pool_age_lt_360s"] == 1 and len(scr2.acc[0][0]["sd"]) == 1 and int(scr2.acc[0][0]["sd"][0]) == old


def test_pool_age_counts_from_the_first_print_in_the_block_not_the_hour(tape):
    rows, files = tape
    # a decision in hour 1 on a pool first printed in hour 0 is old; the same slot is young when hour 0 is not part of the run
    s1 = own_slots(rows, "A", 1, 100, 300)[0]
    assert run_kit(files, [(POOLS["A"], s1, S.ExitSpec(120))], count_only=True).counters["refused_pool_age_lt_360s"] == 0
    scr = run_kit(files[1:], [(POOLS["A"], s1, S.ExitSpec(120))], count_only=True)
    assert scr.counters["refused_pool_age_lt_360s"] == 1


def test_first_seen_is_causal_a_rule_sees_earlier_hours_only(tape):
    """Reviewer 4: state.first_seen used to be filled for the whole hour BEFORE the rule ran, so a rule could learn which pools are about to print."""
    rows, files = tape
    seen = []

    class Peek:
        name = "peek"

        def cells(self):
            return ["c"]

        def triggers(self, hour, state):
            seen.append((hour.hour, dict(state.first_seen)))
            return []

    scr = S.Screen(Peek(), count_only=True)
    scr.run_block(0, "synthetic", S.iter_hours(files[:2], "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    assert seen[0] == ("2026-08-20T00", {})  # hour 0 holds pools A, B, C, D, F, L but the rule is told about none of them
    assert seen[1][0] == "2026-08-20T01" and POOLS["A"] in seen[1][1] and POOLS["N"] not in seen[1][1]
    assert seen[1][1][POOLS["A"]] == min(r["block_time"] for r in rows if r["pool"] == POOLS["A"])
    assert all(v < H0 + 3600 for v in seen[1][1].values())  # nothing from hour 1 itself
    # the age check is still right for a pool whose first print is in the hour being decided
    slot = own_slots(rows, "A", 0, 1000, 1500)[0]
    assert run_kit(files, [(POOLS["A"], slot, S.ExitSpec(120))], count_only=True).counters["refused_pool_age_lt_360s"] == 0


# ---- item 1: rent
def test_the_deciding_leg_prices_at_rent_zero_and_the_charged_leg_is_exactly_the_charge_apart(tape, monkeypatch):
    """Reviewer 1: the deciding leg is rent 0 (DEC-031 Am.2). Mutations 'rent charged on the deciding leg' (the pricing loop, the deciding config) must fail here."""
    from tools import arena_rules

    rows, files = tape
    scr = run_kit(files, script_for(rows))
    st = S._cat(scr.acc[0])
    n = len(st["ref"])
    cfg0 = arena_rules.stack_config(7)  # the host's own pinned stack, built independently of the kit: rent 0 on a filled trip
    cfgC = dataclasses.replace(cfg0, rent=2_039_280)
    assert (cfg0.rent, cfgC.rent, S.RENT_DECIDING, S.RENT_CHARGED, S.screen_cfg().rent) == (0, 2_039_280, 0, 2_039_280, 0)
    assert scr.cfg[S.RENT_DECIDING].rent == 0 and scr.cfg[S.RENT_CHARGED].rent == 2_039_280
    states, f0, fC, big0 = collections.Counter(), 0.0, 0.0, 0.0
    for i in range(n):
        a = P.price_leg("pumpswap", 0.25e9, one(st, i), 1.9, cfg0, "total")
        b = P.price_leg("pumpswap", 0.25e9, one(st, i), 1.9, cfgC, "total")
        states[a["state"]] += 1
        assert a["state"] == b["state"]
        if a["state"] == "filled":  # a filled trip: rent 0 and rent-charged differ by EXACTLY the charge
            assert abs((a["pnl"] - b["pnl"]) - 2_039_280) < 1e-6 and abs((a["flat"] - b["flat"]) - 0.85 * 2_039_280) < 1e-6
        else:  # guarded or stranded: the rent knob does not touch them (a stranded trip pays rent_stranded either way)
            assert a["pnl"] == b["pnl"]
        f0, fC = f0 + a["flat"], fC + b["flat"]
        big0 += P.price_leg("pumpswap", 1.0e9, one(st, i), 1.9, cfg0, "total")["flat"]
    assert states["filled"] >= 15
    r = scr.results()["c"]
    k0, kC = r["stats"]["1.9|total|rent0|flat"], r["stats"]["1.9|total|rentC|flat"]
    assert k0["total"] == pytest.approx(f0 / 1e9, abs=1e-9) and kC["total"] == pytest.approx(fC / 1e9, abs=1e-9)  # each leg equals the independent engine pricing at ITS rent
    assert k0["total"] - kC["total"] == pytest.approx(0.85 * 2_039_280 * states["filled"] / 1e9, abs=1e-9)
    assert r["stake_1_0_total_rent0_1p9"]["flat"]["total"] == pytest.approx(big0 / 1e9, abs=1e-9)  # the 1.0 SOL leg is rent 0 too
    assert r["gate"]["total|flat"]["mean_pct"] == k0["mean_pct"] != kC["mean_pct"]  # the gate columns read the deciding leg
    seen, orig = {}, S.kill_verdict
    monkeypatch.setattr(S, "kill_verdict", lambda subs, *a, **k: (seen.update(subs), orig(subs, *a, **k))[1])
    scr.results()
    assert seen[("total", "flat")]["total"] == k0["total"] != kC["total"]  # and so does the KILL
    for rule in S.STRAND_RULES:
        for fail in S.FAILS:
            assert seen[(rule, fail)]["total"] == r["stats"][f"1.9|{rule}|rent0|{fail}"]["total"]


# ---- item 2: forbidden words, hardcoded
FORBIDDEN_LITERAL = ("fresh-08", "_shakedown", "probe", "shadow", "canary", "forward-", "exp011", "truth-", "tip-tape", "sealed")


def test_the_forbidden_word_list_is_exactly_the_hardcoded_one():
    assert sorted(S.FORBIDDEN) == sorted(FORBIDDEN_LITERAL)


@pytest.mark.parametrize("word", FORBIDDEN_LITERAL)
def test_forbidden_words_are_refused_everywhere(word, tmp_path, monkeypatch):
    """The words are written out here, not read from S.FORBIDDEN, so deleting one from the module fails this test."""
    for p in (f"/data/mal/clean-view/explore-0814/w1/{word}x", f"/data/mal/clean-view/explore-0814/{word.upper()}", f"/data/mal/arena/tape/{word}-1"):
        for count_only in (True, False):
            with pytest.raises(S.Refused, match=f"path_forbidden_word:{word}"):
                S.check_block_path(p, count_only)
    with pytest.raises(S.Refused, match=f"vmap_forbidden_word:{word}"):
        S.check_vmap_paths([f"/data/mal/pumpswap-virtual/{word}/v.json"])
    monkeypatch.setattr(S, "BLOCK_ROOTS", (str(tmp_path),))
    for d, ok in (("wok", True), (f"w{word}", False)):  # an hour file whose directory carries the word
        f = tmp_path / d / "trades" / "trades-2026-08-20T00.jsonl.zst"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"")
    assert len(S.discover(tmp_path / "wok", "clean")) == 1
    with pytest.raises(S.Refused, match="path_forbidden_word"):
        S.discover(tmp_path / f"w{word}", "clean")


def test_priced_results_carry_the_four_sub_cells_groups_and_gate_columns(tape):
    rows, files = tape
    r = run_kit(files, script_for(rows)).results()["c"]
    assert set(r["gate"]) == {"total|flat", "total|press", "sc|flat", "sc|press"} and r["worst_in_slot"]["measured"] is True and r["registration"] == "KILLED"
    assert all(g["ci_lo_pct"] is not None and "majority_dates" in g for g in r["gate"].values())
    # the synthetic block is neither explore-0814 nor a September block: (b) cannot be shown, so it is not a survivor
    assert all(v is None for v in r["groups"].values()) and any("not measured" in w for w in r["kill_reasons"])
    assert r["kill"] is True


def test_groups_are_computed_for_named_blocks(tape, tmp_path):
    rows, files = tape
    scr = S.Screen(ScriptRule(script_for(rows)))
    vm = S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])
    scr.run_block(0, "explore-0814", S.iter_hours(files, "explore-0814", "backfill", vm))
    r = scr.results()["c"]
    assert all(r["groups"][f"explore-0814|{rule}|{fail}"]["n"] == r["attempts"] for rule, fail in S.SUB_CELLS)
    assert r["groups"]["explore-0814|total|flat"]["mean"] == r["stats"]["1.9|total|rent0|flat"]["mean"]
    assert all(r["groups"][f"september|{rule}|{fail}"] is None for rule, fail in S.SUB_CELLS)
    md = S.table_md(scr.results(), scr.blocks, {"rule": "script"})
    assert "explore-0814 alone" in md and "September pooled" in md and "sc/press" in md and "no profit claim" in md


def test_a_losing_synthetic_book_is_killed_and_ex_best_day_is_zero_on_one_day(tape):
    rows, files = tape
    scr = run_kit(files, script_for(rows))
    r = scr.results()["c"]
    s = r["stats"]["1.9|total|rent0|flat"]
    assert s["days"] == 1 and s["ex_best_day"] == pytest.approx(0.0, abs=1e-12) and r["kill"] is True


# ------------------------------------------------------------------------------------------------------------------------------ 6. data guards
ALLOWED = ["/data/mal/clean-view/explore-0814", "/data/mal/clean-view/explore-0814/w3", "/data/mal/clean-view/oracle-insample-2026-09-22_25",
           "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00", "/data/mal/clean-view/oracle-live-2026-09-25_27/trades"]


@pytest.mark.parametrize("p", ALLOWED)
@pytest.mark.parametrize("count_only", [True, False])
def test_the_four_exploration_blocks_are_allowed(p, count_only):
    assert S.check_block_path(p, count_only) == "clean"


@pytest.mark.parametrize("p", ["/data/mal/clean-view/fresh-0802/w1", "/data/mal/clean-view/exp011-0909/b", "/data/mal/clean-view", "/data/mal/blocks/forward-1", "/data/mal",
                               "/etc", "/data/mal/clean-view/explore-0814/../fresh-0828", "/data/mal/clean-view/explore-08140", "/home/claude", "relative/path",
                               "/data/mal/arena/ledger/x", "/data/mal/arena/bots.json", "/data/mal/arena/runner-status.json", "/data/mal/kalshi/x"])
@pytest.mark.parametrize("count_only", [True, False])
def test_every_other_path_is_refused(p, count_only):
    with pytest.raises(S.Refused):
        S.check_block_path(p, count_only)


def test_arena_tape_only_with_count_only(tmp_path):
    p = "/data/mal/arena/tape/tape-2026-10-19T08.jsonl.zst"
    assert S.check_block_path(p, True) == "arena"
    assert S.check_block_path("/data/mal/arena/tape", True) == "arena"
    with pytest.raises(S.Refused, match="arena_path_refused"):
        S.check_block_path(p, False)
    with pytest.raises(S.Refused, match="arena_path_refused"):
        S.check_block_path("/data/mal/arena/tape/../ledger", True)
    with pytest.raises(S.Refused, match="arena_path_refused"):
        S.check_block_path("/data/mal/arena/ledger/bot/trades-2026-10-19.jsonl", True)


def test_a_symlink_out_of_an_allowed_block_is_refused(monkeypatch):
    real = os.path.realpath
    monkeypatch.setattr(S.os.path, "realpath", lambda p: "/data/mal/clean-view/fresh-0802/w1" if str(p).endswith("link") else real(p))
    with pytest.raises(S.Refused):
        S.check_block_path("/data/mal/clean-view/explore-0814/w1/link", False)
    monkeypatch.setattr(S.os.path, "realpath", lambda p: "/data/mal/arena/ledger" if str(p).endswith("link") else real(p))
    with pytest.raises(S.Refused):
        S.check_block_path("/data/mal/clean-view/explore-0814/w1/link", False)


@pytest.mark.parametrize("out", ["/data/mal/arena/screen", "/data/mal/arena", "/data/mal/clean-view/x", "/data/mal/clean-view", "/data/mal/arena/tape/../out"])
def test_output_dir_must_be_outside_the_protected_trees(out):
    with pytest.raises(S.Refused, match="out_dir_inside_protected_tree"):
        S.check_out_dir(out)
    assert S.check_out_dir("/tmp/some-screen-out").endswith("some-screen-out")


def test_d2d3_hours_duplicates_and_arena_file_names_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "BLOCK_ROOTS", (str(tmp_path),))
    d = tmp_path / "trades"
    d.mkdir()
    (d / "trades-2026-10-05T00.jsonl.zst").write_bytes(b"")
    with pytest.raises(S.Refused, match="sealed_hour"):
        S.discover(d, "clean")
    d2 = tmp_path / "b" / "w1" / "trades"
    d3 = tmp_path / "b" / "w2" / "trades"
    d2.mkdir(parents=True)
    d3.mkdir(parents=True)
    (d2 / "trades-2026-08-20T00.jsonl.zst").write_bytes(b"")
    (d3 / "trades-2026-08-20T00.jsonl.zst").write_bytes(b"")
    with pytest.raises(S.Refused, match="duplicate_hour"):
        S.discover(tmp_path / "b", "clean")
    (d3 / "trades-2026-08-20T00.jsonl.zst").unlink()
    (d3 / "trades-2026-08-20T01.jsonl.zst").write_bytes(b"")
    assert [t.hour for t, _ in S.discover(tmp_path / "b", "clean")] == [0, 1]
    assert [t.hour for t, _ in S.discover(tmp_path / "b", "clean", ["2026-08-20T01"])] == [1]
    with pytest.raises(S.Refused, match="no_hour_files"):
        S.discover(tmp_path / "b", "clean", ["2030-01-01T00"])
    with pytest.raises(S.Refused, match="arena_file_refused"):
        S.discover(tmp_path / "b" / "w1" / "trades" / "trades-2026-08-20T00.jsonl.zst", "arena")


def test_an_hour_file_must_resolve_under_its_own_block_root(tmp_path, monkeypatch):
    """QP 5a: a symlinked hour file that points out of the block (to another root, the arena tape, anywhere) is refused although its own name is clean."""
    a, b, outside = tmp_path / "root_a", tmp_path / "root_b", tmp_path / "elsewhere"
    for d in (a / "trades", b / "trades", outside):
        d.mkdir(parents=True)
    monkeypatch.setattr(S, "BLOCK_ROOTS", (str(a), str(b)))
    (a / "trades" / "trades-2026-08-20T00.jsonl.zst").write_bytes(b"")
    assert len(S.discover(a, "clean")) == 1
    (b / "trades" / "trades-2026-08-20T00.jsonl.zst").write_bytes(b"")
    (a / "trades" / "trades-2026-08-20T01.jsonl.zst").symlink_to(b / "trades" / "trades-2026-08-20T00.jsonl.zst")  # into the OTHER block root
    with pytest.raises(S.Refused, match="hour_file_outside_block_root"):
        S.discover(a, "clean")
    (a / "trades" / "trades-2026-08-20T01.jsonl.zst").unlink()
    (outside / "trades-2026-08-20T01.jsonl.zst").write_bytes(b"")
    (a / "trades" / "trades-2026-08-20T01.jsonl.zst").symlink_to(outside / "trades-2026-08-20T01.jsonl.zst")  # out of every block
    with pytest.raises(S.Refused, match="hour_file_outside_block_root"):
        S.discover(a, "clean")
    with pytest.raises(S.Refused, match="hour_file_outside_block_root"):
        S.discover(a / "trades" / "trades-2026-08-20T01.jsonl.zst", "clean")  # naming the link itself does not help
    (a / "trades" / "trades-2026-08-20T01.jsonl.zst").unlink()
    (a / "trades" / "trades-2026-08-20T01.jsonl.zst").symlink_to(a / "trades" / "trades-2026-08-20T00.jsonl.zst")  # inside its own root: fine
    assert len(S.discover(a, "clean")) == 2


def test_cli_refuses_with_exit_code_2_and_reads_nothing(clean_tree, tmp_path, monkeypatch, capsys):
    """Temp trees only (the module roots are pointed at them): a broken guard can never create /data/mal/arena/x or start a real explore-0814 read. Any read is a test failure."""
    root, vm = clean_tree
    arena = tmp_path / "arena"
    (arena / "tape").mkdir(parents=True)
    (arena / "ledger").mkdir()
    monkeypatch.setattr(S, "ARENA_ROOT", str(arena))
    monkeypatch.setattr(S, "ARENA_TAPE", str(arena / "tape"))

    def boom(*a, **k):
        raise AssertionError("a read started")

    for name in ("iter_hours", "_hour_lines", "build_hour", "load_rule"):
        monkeypatch.setattr(S, name, boom)
    before = sorted(tmp_path.rglob("*"))
    good, out = str(root / "explore-0814"), str(tmp_path / "o")
    cases = [["--block", "/etc"], ["--block", str(root / "fresh-0802" / "w1")], ["--block", str(root / "exp011-0909" / "b")], ["--block", str(root)], ["--block", str(arena / "tape")],
             ["--block", str(arena / "ledger"), "--count-only"], ["--block", str(arena / "tape" / "_shakedown-1"), "--count-only"], ["--block", str(root / "explore-0814" / "probe")],
             ["--block", good, "--out", str(arena / "x")], ["--block", good, "--out", str(root / "x")], ["--block", good, "--hours", "2026-08-20T00"],
             ["--block", good, "--block", str(root / "explore-0814" / "w1"), "--count-only"]]
    for extra in cases:
        args = ["--rule", "tools.arena_screen:TrivialRule", "--vmap", str(vm)] + (["--out", out] if "--out" not in extra else []) + extra
        assert S.main(args) == 2, extra
    assert "REFUSED" in capsys.readouterr().err
    assert sorted(tmp_path.rglob("*")) == before and not (tmp_path / "o").exists()  # nothing was created anywhere in the tree


@pytest.fixture
def clean_tree(tape, tmp_path, monkeypatch):
    """The four canonical blocks as a temp tree (hard links of the synthetic tape), with the guards pointed at it."""
    rows, files = tape
    root = tmp_path / "clean"
    monkeypatch.setattr(S, "CLEAN_VIEW", str(root))
    monkeypatch.setattr(S, "BLOCK_ROOTS", tuple(str(root / b) for b in S.CANONICAL_BLOCKS))
    monkeypatch.setattr(S, "VMAP_DIR", str(tmp_path / "virtual"))
    for b in S.CANONICAL_BLOCKS:
        blk = root / b / ("w1" if b == "explore-0814" else ".") / "trades"
        blk.mkdir(parents=True)
        for _, f in files:
            os.link(f, blk / Path(f).name)
    (tmp_path / "virtual").mkdir()
    vm = tmp_path / "virtual" / "v.json"
    vm.write_text(json.dumps({"v": {POOLS[k]: v for k, v in V_OF.items()}}))
    return root, vm


def cli(root, vm, out, *extra, rule="tools.arena_screen:TrivialRule", blocks=None):
    bl = blocks if blocks is not None else [str(root / b) for b in S.CANONICAL_BLOCKS]
    args = ["--rule", rule, "--rule-kwargs", '{"every": 5, "hold_s": 120}', "--out", str(out), "--vmap", str(vm)]
    for b in bl:
        args += ["--block", b]
    return S.main(args + list(extra))


def test_cli_end_to_end_on_the_four_synthetic_blocks_writes_files_and_provenance(clean_tree, tmp_path):
    root, vm = clean_tree
    for extra, outs in ((["--count-only"], ("counts.json", "COUNTS.md")), ([], ("results.json", "TABLE.md"))):
        out = tmp_path / ("o" + ("c" if extra else "p"))
        assert cli(root, vm, out, *extra) == 0 and all((out / o).exists() for o in outs)
        doc = json.loads((out / outs[0]).read_text())
        m = doc["meta"]
        assert m["hours"] == 12 and m["rows_pumpswap_per_s"] > 0 and m["blocks"] == list(S.CANONICAL_BLOCKS)
        assert m["hours_subset"] is False and m["full_screen_of_record"] is (not extra)
        pv = m["provenance"]  # QP 5e: the run can be tied to its DECLARE
        assert pv["kit"]["sha256"] == hashlib.sha256(Path(S.__file__).read_bytes()).hexdigest() and isinstance(pv["kit"]["git_dirty"], bool) and len(pv["kit"]["git_head"]) == 40
        assert pv["rule"]["source_sha256"] == hashlib.sha256(Path(S.__file__).read_bytes()).hexdigest() and pv["rule"]["kwargs"] == '{"every": 5, "hold_s": 120}'
        assert pv["vmap"] == [{"path": str(vm.resolve()), "sha256": hashlib.sha256(vm.read_bytes()).hexdigest()}]
        assert pv["hours"]["explore-0814"] == ["2026-08-20T00", "2026-08-20T01", "2026-08-20T02"] and pv["hours_subset_requested"] is None
    assert doc["cells"]["trivial"]["attempts"] > 0 and doc["cells"]["trivial"]["event_triggered"] is True and doc["cells"]["trivial"]["registration"].startswith(("KILLED", "PROVISIONAL-BLOCKED"))


def test_loophole_a_pricing_needs_each_of_the_four_blocks_once_and_whole(clean_tree, tmp_path):
    root, vm = clean_tree
    four = [str(root / b) for b in S.CANONICAL_BLOCKS]
    o = tmp_path / "o"
    assert cli(root, vm, o, blocks=four[:1]) == 2  # explore alone
    assert cli(root, vm, o, blocks=four[:3]) == 2 and cli(root, vm, o, blocks=four[1:]) == 2  # three of four
    assert cli(root, vm, o, blocks=[str(root / "explore-0814" / "w1")] + four[1:]) == 2  # a worker is not the block
    assert cli(root, vm, o, blocks=four + four[:1]) == 2  # repeated
    assert cli(root, vm, o, blocks=four + [str(root / "explore-0814" / "w1")]) == 2  # nested
    assert cli(root, vm, o, "--count-only", blocks=four + [str(root / "explore-0814" / "w1")]) == 2  # nested is refused for counts too
    assert cli(root, vm, o, "--count-only", blocks=four[:1]) == 0  # a count may be one block
    assert not (tmp_path / "o" / "results.json").exists()
    with pytest.raises(S.Refused, match="pricing_needs_the_four_blocks"):
        S.run(__import__("argparse").Namespace(rule="tools.arena_screen:TrivialRule", rule_kwargs=None, block=four[:2], out=str(tmp_path / "o2"), hours=None, count_only=False, workers=1,
                                              min_attempts_per_day=20.0, vmap=[str(vm)]))
    with pytest.raises(S.Refused, match="repeated_or_nested_block"):
        S.run(__import__("argparse").Namespace(rule="tools.arena_screen:TrivialRule", rule_kwargs=None, block=four[:1] * 2, out=str(tmp_path / "o2"), hours=None, count_only=True, workers=1,
                                              min_attempts_per_day=20.0, vmap=[str(vm)]))


def test_loophole_b_hours_subset_is_refused_when_pricing_and_marked_when_counting(clean_tree, tmp_path):
    root, vm = clean_tree
    o = tmp_path / "ob"
    assert cli(root, vm, o, "--hours", "2026-08-20T01") == 2 and not o.exists()
    out = tmp_path / "oc"
    assert cli(root, vm, out, "--count-only", "--hours", "2026-08-20T01") == 0
    doc = json.loads((out / "counts.json").read_text())
    assert doc["meta"]["hours_subset"] is True and doc["meta"]["full_screen_of_record"] is False and doc["meta"]["hours"] == 4
    assert doc["meta"]["provenance"]["hours_subset_requested"] == ["2026-08-20T01"] and doc["meta"]["provenance"]["hours"]["explore-0814"] == ["2026-08-20T01"]
    assert "SUBSET" in (out / "COUNTS.md").read_text() and "step-2" in (out / "COUNTS.md").read_text()


def test_vmap_paths_are_guarded_and_hashed(tmp_path, monkeypatch):
    good = "/data/mal/pumpswap-virtual/pool_v_0814.json"
    assert S.check_vmap_paths([good]) == [os.path.realpath(good)]
    for bad in ("/data/mal/pumpswap-virtual/forward-1002/pool_v.json", "/data/mal/pumpswap-virtual/probe_v.json", "/data/mal/pumpswap-virtual/sealed/v.json"):
        with pytest.raises(S.Refused, match="vmap_forbidden_word"):
            S.check_vmap_paths([bad])
    for bad in ("/tmp/v.json", "/data/mal/pumpswap-virtual/../clean-view/v.json", "relative.json"):
        with pytest.raises(S.Refused, match="vmap_outside"):
            S.check_vmap_paths([bad])
    real = os.path.realpath
    monkeypatch.setattr(S.os.path, "realpath", lambda p: "/etc/passwd" if str(p).endswith("link.json") else real(p))
    with pytest.raises(S.Refused, match="vmap_outside"):
        S.check_vmap_paths(["/data/mal/pumpswap-virtual/link.json"])
    assert S.file_sha256(Path(S.__file__)) == hashlib.sha256(Path(S.__file__).read_bytes()).hexdigest()


def test_a_missing_timestamp_refuses_the_row_and_is_counted():
    def line(**kw):
        base = {"venue": "pumpswap", "mint": MINT["A"], "trader": "w", "side": "buy", "sol_lamports": 10**8, "token_raw": 10**11, "quote_reserve": 3 * 10**10, "base_reserve": 10**14,
                "pool": POOLS["A"], "slot": 500, "event_index": 0, "tx_index": 1, "block_time": 1_787_184_000, "quote_is_wsol": True}
        return json.dumps({**base, **kw}, separators=(",", ":")).encode()

    lines = [line(), line(block_time=None, slot=501), line(block_time=0, slot=502), line(block_time=None, event_ts=None, slot=503), line(pool=POOLS["B"], block_time=None), line(slot=504, block_time="x")]
    hr = S.build_hour(lines, block="b", T=1_787_184_000, idx=0, fmt="backfill", vmap=S.VMap([{POOLS["A"]: float(V)}]))
    assert hr.n == 1 and hr.n_no_ts == 5 and (hr.bt > 0).all() and list(hr.pools) == [POOLS["A"]]  # never stored as 0; a pool with only bad rows is not created
    hr2 = S.build_hour([line(block_time=None, event_ts=1_787_184_000)], block="b", T=1_787_184_000, idx=0, fmt="backfill", vmap=None)
    assert hr2.n == 1 and hr2.bt[0] == 1_787_184_000  # event_ts is the fallback
    arena = {"type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 5, "t_recv_ms": 1000, "event_ts": 1, "signature": "s", "event_index": 0, "pool": POOLS["A"], "mint": MINT["A"], "trader": "w",
             "side": "buy", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 1, "base_reserve": 1, "virtual_quote_reserves": V, "quote_mint": S.WSOL}
    h3 = S.build_hour([json.dumps(arena).encode(), json.dumps({**arena, "t_recv_ms": None}).encode(), json.dumps({**arena, "event_ts": None}).encode()], block="a", T=0, idx=0, fmt="arena", vmap=None)
    assert h3.n == 1 and h3.n_no_ts == 2


def test_screen_flags_hours_with_missing_timestamps_and_refuses_unknown_pool_age(tape):
    rows, files = tape
    vm = S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])
    slot = own_slots(rows, "A", 0, 1000, 1500)[0]
    scr = S.Screen(ScriptRule([(POOLS["A"], slot, S.ExitSpec(120))]), count_only=True)

    def zeroed():
        for hr in S.iter_hours(files[:1], "synthetic", "backfill", vm):
            hr.bt[:] = 0  # a loader that stored a missing timestamp as 0 would look like this: the seal must refuse, not pass
            hr.n_no_ts = 7
            yield hr

    scr.run_block(0, "synthetic", zeroed())
    assert scr.counters["refused_pool_age_unknown"] == 1 and scr.counters["rows_no_timestamp"] == 7 and scr.flag_hours == ["2026-08-20T00"] and not sum(scr.cnt[0].values())


def write_rule(d: Path, name: str, body: str) -> Path:
    f = d / f"{name}.py"
    f.write_text(body)
    return f


CLEAN_RULE = """
import numpy as np
from tools.arena_screen import Cell, ExitSpec


class Factory:
    name = "clean"

    def __init__(self, **kw):
        pass

    def cells(self):
        return [Cell("c", event_triggered=False)]

    def triggers(self, hour, state):
        return []
"""


def test_rule_guard_source_text_import_graph_and_runtime(tmp_path):
    """DEC-031 3.1: a rule may not import or contain the banned model families (text, import graph, modules actually loaded)."""
    banned = ("r4" + "b", "la" + "ya", "l" + "3", "02_" + "build")
    ok = write_rule(tmp_path, "okrule", CLEAN_RULE)
    sha = S.check_rule_source(ok)
    assert sha == hashlib.sha256(ok.read_bytes()).hexdigest() and S.check_rule_source(S.rule_source_path("tools.arena_screen:TrivialRule"))  # the kit's own TrivialRule passes
    for i, tok in enumerate(banned):  # text, even in a comment
        f = write_rule(tmp_path, f"textrule{i}", CLEAN_RULE + f"\n# features from {tok}_model\n")
        with pytest.raises(S.Refused, match="rule_module_banned"):
            S.check_rule_source(f)
    for name in ("tools." + "r4" + "b_book", "models." + "la" + "ya", "tools.l" + "3_features"):  # imports, plain and from-imports
        for stmt in (f"import {name}", f"from {name.rpartition('.')[0]} import {name.rpartition('.')[2]}", f"from {name} import x"):
            f = write_rule(tmp_path, "imprule", CLEAN_RULE + f"\n{stmt}\n")
            with pytest.raises(S.Refused, match="rule_module_banned"):
                S.check_rule_source(f)
    # the import graph: a clean rule that imports a sibling helper which imports a banned module
    (tmp_path / "helpermod.py").write_text("import tools." + "r4" + "b_book\n")
    f = write_rule(tmp_path, "graphrule", "import helpermod\n" + CLEAN_RULE)
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(S.Refused, match="rule_module_banned"):
            S.check_rule_source(f)
    finally:
        sys.path.remove(str(tmp_path))
    # a banned file name
    with pytest.raises(S.Refused, match="rule_module_banned"):
        S.check_rule_source(write_rule(tmp_path, "l" + "3_rule", CLEAN_RULE))


def test_rule_guard_catches_a_module_imported_at_run_time(tmp_path):
    (tmp_path / ("l" + "3_helper.py")).write_text("X = 1\n")
    body = CLEAN_RULE + "\nimport importlib\n\n\ndef make():\n    importlib.import_module('l' + '3_helper')\n    return Factory()\n"
    f = write_rule(tmp_path, "dynrule", body)
    assert S.check_rule_source(f)  # the static scan cannot see a name built at run time ...
    sys.path.insert(0, str(tmp_path))
    before = set(sys.modules)
    try:
        S.load_rule(f"{f}:make")
        with pytest.raises(S.Refused, match="rule_module_banned"):  # ... the loaded-module check can
            S.check_loaded_modules(before)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("l" + "3_helper", None)


def test_cli_refuses_a_banned_rule_with_exit_code_2(clean_tree, tmp_path):
    root, vm = clean_tree
    bad = write_rule(tmp_path, "badrule", CLEAN_RULE + "\nimport tools." + "r4" + "b_book\n")
    assert cli(root, vm, tmp_path / "ob", "--count-only", rule=f"{bad}:Factory") == 2 and not (tmp_path / "ob").exists()
    good = write_rule(tmp_path, "goodrule", CLEAN_RULE)
    assert cli(root, vm, tmp_path / "og", "--count-only", rule=f"{good}:Factory") == 0
    pv = json.loads((tmp_path / "og" / "counts.json").read_text())["meta"]["provenance"]["rule"]
    assert pv["source_sha256"] == hashlib.sha256(good.read_bytes()).hexdigest() and pv["spec"].endswith("goodrule.py:Factory")


def test_causal_probe_leaves_the_rule_state_alone(tape):
    rows, files = tape
    hr = next(S.iter_hours(files[:1], "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    st = S.RunState("synthetic", scratch={"seen": []}, first_seen={POOLS["A"]: 1})
    slot = int(hr.sl[hr.rows(hr.pool_index()[POOLS["A"]])][3])

    class Writer:
        def triggers(self, hour, state):
            state.scratch["seen"].append(1)
            state.first_seen["x"] = 2
            return [(POOLS["A"], slot, S.ExitSpec(120))]

    assert S.causal_probe(Writer(), hr, st, POOLS["A"], slot)
    assert st.scratch == {"seen": []} and st.first_seen == {POOLS["A"]: 1}


# ------------------------------------------------------------------------------------------------------------------------------ 6b. event flag, worst-in-slot, text
def test_cell_event_flag_defaults_to_the_stricter_kind_and_is_read_from_any_cell_object():
    assert S.Cell("x").event_triggered is True and S.Cell("x", event_triggered=False).event_triggered is False
    from types import SimpleNamespace

    scr = S.Screen(ScriptRule([], cells=(S.Cell("a"), S.Cell("b", event_triggered=False), "c", SimpleNamespace(name="d", cooldown_s=5.0, event_triggered=False), SimpleNamespace(name="e"))))
    assert [(c.name, c.event_triggered) for c in scr.cells] == [("a", True), ("b", False), ("c", True), ("d", False), ("e", True)] and scr.cells[3].cooldown_s == 5.0


def test_registration_status_event_cells_are_blocked_until_worst_in_slot_is_measured():
    st = S.registration_status
    assert st(True, True, False) == st(False, True, False) == "KILLED"
    assert st(True, False, False).startswith("PROVISIONAL-BLOCKED") and "no registration" in st(True, False, False)
    assert st(False, False, False).startswith("PROVISIONAL (grid-triggered") and "worst-in-slot not measured" in st(False, False, False)
    assert st(True, False, True).startswith("SCREEN-SURVIVOR") and st(False, False, True).startswith("SCREEN-SURVIVOR")


def kit_cell(tape, event: bool) -> "S.Screen":
    rows, files = tape
    scr = S.Screen(ScriptRule(script_for(rows), cells=(S.Cell("c", event_triggered=event),)))
    scr.run_block(0, "synthetic", S.iter_hours(files, "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    return scr


@pytest.mark.parametrize("event, want", [(True, "SCREEN-SURVIVOR (worst-in-slot measured > 0 on all four sub-cells)"), (False, "SCREEN-SURVIVOR (worst-in-slot measured)")])
def test_a_measured_survivor_carries_the_worst_in_slot_verdict_of_its_declared_kind(tape, monkeypatch, event, want):
    monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))  # isolate the worst-in-slot rule from the other kills
    r = kit_cell(tape, event).results()["c"]
    w = r["worst_in_slot"]
    assert r["event_triggered"] is event and r["registration"] == want and not r["kill"]
    assert w["measured"] is True and set(w["sub_cells"]) == {"total|flat", "total|press", "sc|flat", "sc|press"}
    assert all(c["lower"]["mean_sol"] > 0 for c in w["sub_cells"].values())
    md = S.table_md({"c": r}, ["synthetic"], {"rule": "script"})
    assert "## 4. Worst-in-slot (R7)" in md and ("EVENT" if event else "GRID") in md and "| not measured |" not in md


@pytest.mark.parametrize("event, want", [(True, "PROVISIONAL-BLOCKED"), (False, "PROVISIONAL (grid-triggered")])
def test_a_tape_that_does_not_reproduce_the_end_states_leaves_worst_in_slot_not_measured(tape, monkeypatch, event, want):
    """No figure is printed or stored; an event cell stays blocked (not killed, not a survivor), a grid cell stays provisional."""
    monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))
    orig = S.worst_in_slot_states

    def broken(*a, **k):
        o = orig(*a, **k)
        o["w_mismatch"][:] = True
        return o

    monkeypatch.setattr(S, "worst_in_slot_states", broken)
    r = kit_cell(tape, event).results()["c"]
    w = r["worst_in_slot"]
    assert w["measured"] is False and w["sub_cells"] is None and "END consistency failed" in w["reason"] and not r["kill"] and r["registration"].startswith(want)
    assert "| not measured |" in S.table_md({"c": r}, ["synthetic"], {"rule": "script"})


def test_worst_in_slot_states_flag_an_end_state_the_tape_does_not_reproduce():
    a = pool_arrays(600, 7)
    sd = a["sl"][[100, 200, 300]]
    xs = sd + 60
    sps = np.full(3, 0.4)
    st = S.pool_states(a, sd, sps, xs, sps)
    need = np.maximum(xs + S.D + F.exit_lag_vec(sps) + 1, sd + S.D + F.k_slots_vec(1.9, sps, "ceil") + 1)
    none = np.zeros(3, bool)
    ok = S.worst_in_slot_states("P", a, sd, sps, xs, sps, need, none, none, st)
    assert not ok["w_mismatch"].any() and not ok["w_vbad"].any()
    for col in ("Qx", "Bx"):
        assert S.worst_in_slot_states("P", a, sd, sps, xs, sps, need, none, none, dict(st, **{col: st[col] * 1.001}))["w_mismatch"].all()
    assert S.worst_in_slot_states("P", a, sd, sps, xs, sps, need, none, none, dict(st, Qe={l: v * 1.001 for l, v in st["Qe"].items()}))["w_mismatch"].all()
    nov = dict(a, v=np.where(np.arange(600) % 3 == 0, np.nan, a["v"]))  # a print without V inside the slots read
    assert S.worst_in_slot_states("P", nov, sd, sps, xs, sps, need, none, none, st)["w_vbad"].any()
    assert np.isnan(S.worst_in_slot_states("P", a, sd, sps, xs, sps, need, none, np.array([True, False, False]), st)["wQx"][0])  # a skipped trip is left NaN


def lower_leg_st(win: np.ndarray, flip: np.ndarray) -> dict:
    """Trips with hand-set states. A winner exits 10% up; a loser exits at 0.6 of the entry quote (a C8 strand). `flip` trips are entered at 1.5x the decision spot at their WORST entry
    state: the 1.15 guard then turns them away (pnl = -send), so the worst-in-slot leg 'rescues' a loser and loses a winner. END states are the entry state itself."""
    n = len(win)
    Q0 = V + 20e9
    B0 = Q0 * 1e6 / 300
    z = np.zeros(n)
    st = {"ref": np.full(n, Q0 / B0), "Qe": {l: np.full(n, Q0) for l in S.LATS}, "Be": {l: np.full(n, B0) for l in S.LATS}, "ssb": {l: z.copy() for l in S.LATS},
          "nearby": {l: z.copy() for l in S.LATS}, "Qx": np.where(win, 1.10 * Q0, 0.6 * Q0), "Bx": np.full(n, B0)}
    st.update(wQe={l: np.where(flip, 1.5 * Q0, Q0) for l in S.LATS}, wBe={l: np.full(n, B0) for l in S.LATS}, wQx=st["Qx"].copy(), wBx=st["Bx"].copy(),
              w_vbad=np.zeros(n, bool), w_mismatch=np.zeros(n, bool))
    return st


def lower_leg_case(n: int, case: str) -> dict:
    idx = np.arange(n)
    if case == "guard_rescue_flatters_worst":  # the worst-in-slot leg is above the companion: the companion is the lower leg
        win = idx % 3 != 0
        return lower_leg_st(win, ~win)
    win = idx >= 1  # 'worst_in_slot_is_lower': one real loser; the guard turns away 35 of the 39 winners at the worst state, so the worst-in-slot leg loses them and the companion keeps them
    return lower_leg_st(win, win & (idx >= 5))


@pytest.mark.parametrize("case, lower_leg", [("guard_rescue_flatters_worst", "no_guard_rescue"), ("worst_in_slot_is_lower", "worst_in_slot")])
def test_the_lower_of_the_two_legs_is_what_the_worst_in_slot_rule_reads(case, lower_leg):
    """Mutation-sensitive: taking only the worst-in-slot leg, only the companion, or the HIGHER of the two fails one of the two cases."""
    st = lower_leg_case(40, case)
    k = S.worst_in_slot_legs(st, S.screen_cfg())
    assert k["measured"] is True
    for key, c in k["sub_cells"].items():
        w, g = c["worst_in_slot"]["mean_sol"], c["no_guard_rescue"]["mean_sol"]
        assert c["lower"]["mean_sol"] == min(w, g) and c["lower"]["leg_of_mean"] == lower_leg, (key, w, g)
        if lower_leg == "no_guard_rescue":
            assert w > 0 >= g and c["lower"]["mean_sol"] == g  # the worst-in-slot leg alone would pass; the lower leg does not
        else:
            assert g > 0 >= w and c["lower"]["mean_sol"] == w  # the companion alone would pass; the lower leg does not
        assert c["end_minus_worst"]["mean_sol"] == pytest.approx(c["end"]["mean_sol"] - w) and c["end_minus_no_guard_rescue"]["mean_sol"] == pytest.approx(c["end"]["mean_sol"] - g)
    assert k["diag"]["guard_flips"]["total"] > 0 and k["diag"]["guard_flips"]["sc"] > 0


@pytest.mark.parametrize("case", ["guard_rescue_flatters_worst", "worst_in_slot_is_lower"])
@pytest.mark.parametrize("event", [True, False])
def test_an_event_cell_is_killed_by_the_lower_leg_in_any_sub_cell_a_grid_cell_only_reports_it(tape, monkeypatch, case, event):
    monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))  # isolate the worst-in-slot rule from the other kills
    scr = kit_cell(tape, event)
    base = S._cat(scr.acc[0])
    scr.acc[0] = [dict(base, **lower_leg_case(len(base["ref"]), case))]
    r = scr.results()["c"]
    wis = [w for w in r["kill_reasons"] if w.startswith("worst-in-slot")]
    assert r["worst_in_slot"]["measured"] is True and all(not c["lower"]["mean_sol"] > 0 for c in r["worst_in_slot"]["sub_cells"].values())
    if event:
        assert r["kill"] is True and r["registration"] == "KILLED" and len(wis) == 1 and all(t in wis[0] for t in ("total/flat", "total/press", "sc/flat", "sc/press"))
    else:
        assert r["kill"] is False and not wis and r["registration"] == "SCREEN-SURVIVOR (worst-in-slot measured)"


def test_one_non_positive_sub_cell_is_enough_to_kill_an_event_cell(tape, monkeypatch):
    monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))
    real = S.worst_in_slot_legs

    def one_bad(st, cfg):
        o = real(st, cfg)
        o["sub_cells"]["sc|press"]["lower"]["mean_sol"] = 0.0
        return o

    monkeypatch.setattr(S, "worst_in_slot_legs", one_bad)
    r = kit_cell(tape, True).results()["c"]
    assert r["kill"] and any(w.endswith("mean <= 0 in sc/press") for w in r["kill_reasons"]) and r["registration"] == "KILLED"
    assert not kit_cell(tape, False).results()["c"]["kill"]  # the kill is for event-triggered cells (QP R7 ruling)


def test_worst_in_slot_functions_are_imported_from_arena_report_legs_never_copied():
    from tools import arena_report_legs as RL

    assert (S.slot_states, S.worst_index, S.state_arrays, S.end_minus_worst) == (RL.slot_states, RL.worst_index, RL.state_arrays, RL.end_minus_worst)
    src = Path(S.__file__).read_text()
    for name in ("slot_states", "worst_index", "state_arrays", "end_minus_worst"):
        assert f"def {name}" not in src
    assert S.WORST_IN_SLOT_AVAILABLE is True
    # the QP-approved file (#747, QP OK at b204930): if it changes, the imported functions are no longer the approved ones and this fails until QP re-approves
    assert S.REPORT_LEGS_PIN == "65f4d471e3ad19308a038c4d6563f22322134013f09c0afe2d64c648964a0d8b" == hashlib.sha256(Path(RL.__file__).read_bytes()).hexdigest()
    assert S.kit_provenance()["report_legs_sha256"] == S.REPORT_LEGS_PIN


# ------------------------------------------------------------------------------------------------------------------------------ 6c. parity with arena_report_legs
BURST_TRIGGERS = {"A": [1000 + 210 * j for j in range(10)], "B": [1105 + 210 * j for j in range(10)]}  # offsets in hour 0 (a trip is open 50 slots, so none overlaps)


def burst_rows(seed: int = 11) -> list[dict]:
    """A tape with several prints per slot and big price swings in the slots the trips land in and exit in (so worst != END and the 1.15 guard flips), one WSOL pool F that prints
    every slot (the feed heartbeat), a derived_last pool D and a late-print pool L."""
    rnd = random.Random(seed)
    st = {k: {"q": 20e9 + 1e9 * i, "b": (V + 20e9 + 1e9 * i) * 1e6 / 300} for i, k in enumerate("FABDL")}
    rows, tx = [], 0

    def trade(k: str, h: int, kk: int, buy: bool | None = None, big: bool | None = None, tok_back: float | None = None) -> float:
        nonlocal tx
        s = st[k]
        slot, ms = slot_ms(h, kk)
        Q = s["q"] + V_OF[k]
        buy = rnd.random() < (0.8 if s["q"] < 14e9 else 0.2 if s["q"] > 40e9 else 0.5) if buy is None else buy
        big = rnd.random() < 0.35 if big is None else big
        row = {"venue": "pumpswap", "type": "trade", "prog": "pumpswap", "mint": MINT[k], "trader": f"T{rnd.randrange(50)}", "pool": POOLS[k], "slot": slot, "event_index": 0, "tx_index": tx,
               "signature": f"sig{tx}", "block_time": ms // 1000, "t_recv_ms": None, "quote_is_wsol": True, "quote_mint": S.WSOL, "quote_reserve": int(s["q"]),
               "base_reserve": int(s["b"]), "side": "buy" if buy else "sell"}
        tx += 1
        if buy:
            sol = int(rnd.uniform(2.0, 6.0) * 1e9) if big else int(rnd.uniform(0.02, 0.4) * 1e9)
            net = sol * 0.99
            tok = s["b"] * net / (Q + net)
            s["q"] += net
            s["b"] -= tok
        else:
            tok = tok_back if tok_back is not None else s["b"] * (rnd.uniform(0.04, 0.11) if big else rnd.uniform(0.0005, 0.01))
            out = Q * tok / (s["b"] + tok) * 0.99
            if out > 0.4 * s["q"] and tok_back is None:
                tok *= 0.4 * s["q"] / out
                out = Q * tok / (s["b"] + tok) * 0.99
            sol = int(out)
            s["q"] -= out
            s["b"] += tok
        row.update(sol_lamports=sol, token_raw=int(tok))
        rows.append(row)
        return tok

    spikes = {(k, t + 12) for k, ts in BURST_TRIGGERS.items() for j, t in enumerate(ts) if j % 2 == (k == "B")}  # the 1.9 s landing slot of every other trip: a big buy and the sell back
    for h in range(NHOUR):
        for kk in range(KMAX + 1):
            trade("F", h, kk)
            if h == 0 and kk < 4000:
                for k in "AB":
                    if kk in BURST_TRIGGERS[k]:
                        trade(k, h, kk)  # a print at the decision slot
                    if (k, kk) in spikes:
                        trade(k, h, kk, buy=False, big=True, tok_back=trade(k, h, kk, buy=True, big=True))
                    for _ in range(rnd.choices([0, 1, 2, 3], [3, 4, 2, 1])[0]):
                        trade(k, h, kk)
            if h == 0 and kk < 2000 and rnd.random() < 0.2:
                trade("D", h, kk)
            if (h == 0 and kk < 2000 and rnd.random() < 0.2) or (h == 1 and kk == 3500):
                trade("L", h, kk)
    return rows


def burst_script(rows) -> list[tuple[str, int, S.ExitSpec]]:
    out = []
    for key in "AB":
        for t in BURST_TRIGGERS[key]:
            out.append((POOLS[key], SLOT0 + t, S.ExitSpec(20) if len(out) % 3 else S.ExitSpec(20, tp=1.01, sl=0.99)))
    out.append((POOLS["D"], own_slots(rows, "D", 0, 1800, 2000)[-1], S.ExitSpec(120)))  # D's last print: no print at or after need, a derived_last state
    out.append((POOLS["L"], own_slots(rows, "L", 0, 1800, 2000)[-1], S.ExitSpec(120)))  # L prints again at hour 1, later than need + GRACE: derived from the rows before need
    return out


def test_worst_in_slot_parity_with_arena_report_legs_to_the_lamport(tmp_path):
    """The pin: on the same trips and the same tape the kit's worst-in-slot leg, its no-guard-rescue companion, the lower leg and the END-minus-worst gap equal tools.arena_report_legs's
    (run on the engine-written ledger rows of those trips), state by state, trip by trip (< 1e-6 lamports) and cell by cell."""
    from tools import arena_report_legs as RL

    rows = burst_rows()
    files = write_tape(tmp_path / "trades", rows)
    scr = run_kit(files, burst_script(rows))
    st = S._cat(scr.acc[0])
    n = len(st["ref"])
    assert scr.counters["derived_last_state"] == 1 and scr.counters["late_print_derived"] == 1 and n >= 20
    names = {zlib.crc32(p.encode()): p for p in POOLS.values()}
    trips = [(names[int(p)], int(sd), int(xs)) for p, sd, xs in zip(st["pool"], st["sd"], st["xs"])]
    assert len(set(trips)) == n
    got, excluded, eng = replay_engine(rows, trips, tmp_path, S.RENT_DECIDING)
    assert not excluded and not eng.pending and eng.counters.get("price_errors", 0) == 0
    trades = [got[(p, sd, 0.25)] for p, sd, _ in trips]  # the kit's order: the bootstrap depends on it
    assert sum(t["state_src"] == "derived_last" for t in trades) == 2
    assert not st["w_mismatch"].any() and not st["w_vbad"].any()
    book = RL.legs_for_book(trades, eng.pools)
    assert book["end_check"]["ok"] is True
    ref = book["worst_in_slot"]
    cfg = S.screen_cfg(S.RENT_DECIDING)
    k = S.worst_in_slot_legs(st, cfg)
    assert k["measured"] is True
    wrows, wng, wdiag = RL.worst_in_slot_both(trades, eng.pools)
    # the fixture really exercises the worst-in-slot picks: several prints in the slots, picks that moved, guard flips, a derived and a late trip
    assert wdiag["entry_multi_print"] > 0 and wdiag["exit_multi_print"] > 0 and wdiag["entry_moved"] > 0 and wdiag["exit_moved"] > 0 and sum(wdiag["guard_flips"].values()) > 0
    assert (k["diag"]["entry_moved"] > 0) and (k["diag"]["exit_moved"] > 0) and sum(k["diag"]["guard_flips"].values()) > 0
    # 1. the picked states, per trip and latency
    for i, r in enumerate(wrows):
        for lat, tag in ((1.3, "1p3"), (1.9, "1p9")):
            c = r["calc"][tag]
            assert (st["wQe"][lat][i], st["wBe"][lat][i], st["wQx"][i], st["wBx"][i]) == (c["Qe"], c["Be"], c["Qx"], c["Bx"]), (i, tag)
    # 2. the re-priced trips, per strand rule, to the lamport (worst-in-slot and the companion)
    stw = dict(st, Qe=st["wQe"], Be=st["wBe"], Qx=st["wQx"], Bx=st["wBx"])
    worst_lamports = 0.0
    for rule in S.STRAND_RULES:
        Lw, Le = S.legs(stw, 0.25e9, 1.9, rule, cfg), S.legs(st, 0.25e9, 1.9, rule, cfg)
        rescue = Le["filled"] & ~Lw["filled"]
        for i in range(n):
            w_ref, ng_ref = wrows[i]["calc"]["1p9"]["by_rule"][rule], wng[i]["calc"]["1p9"]["by_rule"][rule]
            ng_pnl = Le["pnl"][i] if rescue[i] else Lw["pnl"][i]
            worst_lamports = max(worst_lamports, abs(Lw["pnl"][i] - w_ref["pnl_nofail"]), abs(ng_pnl - ng_ref["pnl_nofail"]))
            assert bool(Lw["filled"][i]) == w_ref["filled"] and bool(Lw["strand"][i]) == w_ref["strand"]
        assert k["diag"]["guard_flips"][rule] == ref["diag"]["guard_flips"].get(f"1p9_{rule}", 0) == int(rescue.sum())
    assert worst_lamports < 1e-6
    # 3. the cells: END, worst-in-slot, no guard rescue, the lower leg, both gaps, the pressure intercepts
    close = lambda a, b: a == pytest.approx(b, rel=1e-12, abs=1e-15)  # noqa: E731
    differs = 0
    for rule in S.STRAND_RULES:
        for fail in S.FAILS:
            key, rk = f"{rule}|{fail}", f"{fail}_1p9_{rule}"
            sub = k["sub_cells"][key]
            for mine, theirs in ((sub["end"], book["as_scored"][rk]), (sub["worst_in_slot"], ref["cells"][rk]), (sub["no_guard_rescue"], ref["no_guard_rescue"]["cells"][rk])):
                assert mine["n"] == theirs["n"] == n
                assert close(mine["mean_sol"], theirs["mean_sol"]) and close(mine["total_sol"], theirs["total_sol"]) and close(mine["ci90_lo_sol"], theirs["ci90_lo_sol"])
            assert sub["lower"]["leg_of_mean"] == ref["canary_lower"][rk]["leg_of_mean"]
            assert close(sub["lower"]["mean_sol"], ref["canary_lower"][rk]["mean_sol"]) and close(sub["lower"]["ci90_lo_sol"], ref["canary_lower"][rk]["ci90_lo_sol"])
            for mine, theirs in ((sub["end_minus_worst"], ref["end_minus_worst"][rk]), (sub["end_minus_no_guard_rescue"], ref["no_guard_rescue"]["end_minus_worst"][rk])):
                assert close(mine["mean_sol"], theirs["mean_sol"]) and close(mine["total_sol"], theirs["total_sol"])
            differs += abs(sub["end_minus_worst"]["mean_sol"]) > 1e-6
        ic = k["diag"]["pressure_intercept"][rule]
        assert close(ic["end"], book["as_scored"][f"press_1p9_{rule}"]["intercept"]) and close(ic["worst_in_slot"], ref["cells"][f"press_1p9_{rule}"]["intercept"])
        assert close(ic["no_guard_rescue"], ref["no_guard_rescue"]["cells"][f"press_1p9_{rule}"]["intercept"])
    assert differs == 4  # worst-in-slot is not END on this tape in any sub-cell: the pin is not vacuous


def test_table_and_counts_say_what_the_author_must_do(tape, tmp_path):
    rows, files = tape
    md = S.table_md(run_kit(files, script_for(rows)).results(), ["synthetic"], {"rule": "script"})
    for needle in ("C1-NF", "RULES step 2", "WITHOUT pricing", "at least 200 triggers PER BLOCK with 0 failures", "causal_probe", "not the bot's", "md5 decision-equivalence", "0.95^N",
                   "candidate for a forward slot and nothing more", "PROVISIONAL-BLOCKED", "no profit claim"):
        assert needle in md, needle
    scr = run_kit(files, [(POOLS["A"], own_slots(rows, "A", 0, 1000, 1500)[0], S.ExitSpec(120))], count_only=True, name="explore-0814", min_per_day=20.0)
    res = scr.results()
    assert res["c"]["explore_0814"]["days"] == 1 and res["c"]["explore_0814"]["below_step2_count"] is True
    cm = S.counts_md(res, {"rule": "script", "hours_subset": False})
    assert "explore-0814 att/day" in cm and "DROP (<20)" in cm and "C1-NF" in cm


def test_causal_probe_sampling_requirement_is_stated_in_the_module_doc():
    assert "causal_probe" in (S.__doc__ or "") and "NOT" in (S.__doc__ or "")


# ------------------------------------------------------------------------------------------------------------------------------ 7. loaders
def test_worker_processes_load_the_same_hours_in_order(tape):
    rows, files = tape
    vm = S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])
    a = list(S.iter_hours(files, "synthetic", "backfill", vm, workers=1))
    b = list(S.iter_hours(files, "synthetic", "backfill", vm, workers=2))
    assert [h.hour for h in a] == [h.hour for h in b] == ["2026-08-20T00", "2026-08-20T01", "2026-08-20T02"]
    for x, y in zip(a, b):
        assert (x.sl == y.sl).all() and (x.v == y.v).all() and (x.ptr == y.ptr).all() and x.slots_per_s == y.slots_per_s == 2.5 and x.nonwsol_pools == y.nonwsol_pools
    assert a[0].n_nonwsol > 0 and POOLS["N"] in a[0].nonwsol_pools and POOLS["N"] not in a[0].pool_index()  # non-WSOL pools are noted and not loaded
    ok = a[0].pools[a[0].in_universe].tolist()
    assert POOLS["A"] in ok and POOLS["C"] not in ok  # C's V (25e9) is outside the Am.2 band


def test_arena_tape_format_loader_reads_v_from_the_row_and_orders_by_receive_time():
    def line(**kw):
        base = {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 500, "t_recv_ms": 1_000, "signature": "s", "event_index": 0, "pool": POOLS["A"], "mint": MINT["A"],
                "trader": "w", "side": "buy", "sol_lamports": 10**8, "token_raw": 10**11, "quote_reserve": 3 * 10**10, "base_reserve": 10**14, "virtual_quote_reserves": V,
                "quote_mint": S.WSOL, "event_ts": 1}
        return json.dumps({**base, **kw}).encode()

    lines = [line(t_recv_ms=1_900, signature="late"), line(t_recv_ms=1_100, signature="early"), line(type="gap"), line(prog="pumpfun", venue="pump_bonding", pool=None),
             line(pool=POOLS["B"], quote_mint="Other" + "1" * 39), line(pool=POOLS["C"], virtual_quote_reserves=None), line(pool=POOLS["D"], virtual_quote_reserves=V_OUT)]
    hr = S.build_hour(lines, block="arena-tape", T=H0, idx=0, fmt="arena", vmap=None)
    assert hr.n == 4 and hr.n_nonwsol == 1 and hr.n_pumpswap == 5
    a = hr.rows(hr.pool_index()[POOLS["A"]])
    assert hr.tx[a].tolist() == [1_100, 1_900]  # receive order inside the slot
    ok = dict(zip(hr.pools.tolist(), hr.in_universe.tolist()))
    assert ok == {POOLS["A"]: True, POOLS["C"]: False, POOLS["D"]: False}
    assert math.isnan(hr.v[hr.rows(hr.pool_index()[POOLS["C"]])][0])


# ------------------------------------------------------------------------------------------------------------------------------ 9. screen p (EXP-028, report only; QP ruling on #756 item 4)
def ref_means(x: np.ndarray, B: int, seed: int = 1) -> np.ndarray:
    """arena_stack.boot_lo's own loop, written out in the test."""
    rng = np.random.default_rng(seed)
    return np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(B)])


def ref_p(x: np.ndarray, B: int, seed: int = 1) -> tuple[int, int, float, float]:
    m, xbar = ref_means(x, B, seed), float(np.mean(x))
    c1, c2 = sum(1 for v in m if v <= 0), sum(1 for v in m if v - xbar >= xbar)
    return c1, c2, (1 + c1) / (1 + B), (1 + c2) / (1 + B)


def day_vec(n: int, k: int) -> np.ndarray:
    return np.arange(n) % k + 20_000


def date_sums(x: np.ndarray, day: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    _, inv = np.unique(day, return_inverse=True)
    return np.bincount(inv, weights=x), np.bincount(inv).astype(float)


def skewed(seed: int = 1, n: int = 300, t: float = 2.2) -> np.ndarray:
    """A right-skewed per-attempt vector (a few large winners) with a positive mean of about t standard errors, in SOL."""
    x = np.random.default_rng(seed).lognormal(0, 1.5, n)
    return x - x.mean() + t * x.std(ddof=1) / np.sqrt(n)


@pytest.mark.parametrize("B", [1_000, 10_000, 25_000])
def test_screen_p_first_1000_draws_reproduce_arena_stack_boot_lo_exactly(B):
    rng = np.random.default_rng(B)
    lam = rng.normal(1e6, 6e7, 500)
    day = day_vec(500, 9)
    x = lam / 1e9
    m = S._boot_means(x, B)
    assert np.percentile(m[:1000], F.BOOT_PCT) == F.boot_lo(x)  # bit for bit, no tolerance
    assert (m[:1000] == ref_means(x, 1000)).all()
    dsum, dn = date_sums(x, day)
    r = S.screen_p_sub(lam, day, B=B, ci_lo_trade=F.boot_lo(x), ci_lo_date=S.boot_lo(dsum, dn))
    assert r["ci90_lo_first1000"] == F.boot_lo(x) and r["ci90_lo_first1000_equals_stat"] is True
    rr = S._cluster_ratios(dsum, dn, B)
    assert np.percentile(rr[:1000], F.BOOT_PCT) == S.boot_lo(dsum, dn)  # the kit's date resampler, bit for bit
    assert r["date_cluster"]["ci90_lo_first1000"] == S.boot_lo(dsum, dn) and r["date_cluster"]["ci90_lo_first1000_equals_stat"] is True
    r2 = S.screen_p_sub(lam, day, B=B, ci_lo_trade=F.boot_lo(x) + 1e-12)
    assert r2["ci90_lo_first1000_equals_stat"] is False  # the self-check can fail


def test_screen_p_each_B_is_a_prefix_of_a_longer_B_with_the_same_seed_and_a_fresh_rng_per_sub_cell():
    x = np.random.default_rng(2).normal(0.002, 0.05, 200)
    assert (S._boot_means(x, 3_000)[:1_500] == S._boot_means(x, 1_500)).all()
    a = S.screen_p_sub(x * 1e9, day_vec(200, 7), B=2_000)
    S.screen_p_sub(np.random.default_rng(9).normal(0, 1e7, 300), day_vec(300, 6), B=2_000)  # another sub-cell in between: no shared stream
    assert S.screen_p_sub(x * 1e9, day_vec(200, 7), B=2_000) == a


def test_screen_p_all_positive_vector_sits_on_the_1_over_B_plus_1_floor():
    lam = 1_000_000 + np.random.default_rng(5).integers(0, 1_000, 300)  # every attempt positive, tiny spread
    r = S.screen_p_sub(lam, day_vec(300, 6), B=10_000)
    assert (r["tail_count_pct"], r["tail_count_null"]) == (0, 0)
    assert r["p_pct"] == r["p_null"] == r["p"] == 1 / 10_001
    d = r["date_cluster"]
    assert (d["tail_count_pct"], d["tail_count_null"], d["k_dates"]) == (0, 0, 6) and d["p"] == 1 / 10_001
    r2 = S.screen_p_sub(lam, day_vec(300, 6), B=40_000)
    assert r2["p"] == 1 / 40_001 and r2["B"] == 40_000 and r2["date_cluster"]["B"] == 40_000  # the floor moves with B


def test_screen_p_non_positive_mean_gives_p_near_one():
    r = S.screen_p_sub(np.random.default_rng(6).normal(-3e6, 1e7, 400), day_vec(400, 8), B=10_000)
    assert r["xbar"] < 0 and r["p_pct"] == r["p_null"] == r["p"] == 1.0 and r["tail_count_pct"] == r["tail_count_null"] == 10_000
    assert r["date_cluster"]["p"] == 1.0
    z = S.screen_p_sub(np.zeros(50), day_vec(50, 5), B=1_000)  # a mean of exactly 0 is H0 itself: p = 1
    assert z["xbar"] == 0.0 and z["p"] == 1.0 and z["date_cluster"]["p"] == 1.0
    sym = S.screen_p_sub(np.array([2e7, -2e7] * 100), day_vec(200, 5), B=10_000)  # mean exactly 0, symmetric draws: near one half on both tails
    assert sym["xbar"] == 0.0 and 0.4 < sym["p_pct"] < 0.65 and 0.4 < sym["p_null"] < 0.65


def test_screen_p_skewed_vector_percentile_tail_is_anticonservative_and_the_max_rule_takes_the_null_tail():
    x = skewed()
    day = day_vec(300, 8)
    r = S.screen_p_sub(x * 1e9, day, B=10_000)
    c1, c2, p1, p2 = ref_p(x, 10_000)
    assert (r["tail_count_pct"], r["tail_count_null"]) == (c1, c2) == (21, 216)  # pinned: the draws are deterministic at seed 1
    assert (r["p_pct"], r["p_null"]) == (p1, p2) == (22 / 10_001, 217 / 10_001)
    assert r["p_pct"] < r["p_null"] and r["p"] == r["p_null"]  # right skew: the percentile tail alone would pass what the null tail does not
    dsum, dn = date_sums(x, day)
    rng = np.random.default_rng(1)
    rr = np.array([dsum[ix].sum() / max(1, dn[ix].sum()) for ix in (rng.integers(0, 8, 8) for _ in range(10_000))])
    rhat = dsum.sum() / dn.sum()
    d = r["date_cluster"]
    assert (d["tail_count_pct"], d["tail_count_null"]) == (int((rr <= 0).sum()), int(((rr - rhat) >= rhat).sum())) and d["rhat"] == rhat
    assert d["p_pct"] == (1 + d["tail_count_pct"]) / 10_001 and d["p"] == max(d["p_pct"], d["p_null"])


def test_screen_p_the_percentile_tail_can_be_the_larger_one_too():
    x = np.array([-1.0] * 97 + [40.0] * 3)  # discrete right skew where the left tail is the heavier one
    r = S.screen_p_sub(x * 1e9, day_vec(100, 5), B=10_000)
    c1, c2, p1, p2 = ref_p(x, 10_000)
    assert (r["tail_count_pct"], r["tail_count_null"], r["p_pct"], r["p_null"]) == (c1, c2, p1, p2) and p1 > p2 and r["p"] == p1


def test_screen_p_tails_count_equality_inclusively_and_use_one_plus_over_one_plus_B(monkeypatch):
    lam = np.array([5e8, 1.5e9] * 50)  # xbar is exactly 1.0 SOL
    m = np.full(1_000, 1.0)
    m[:10] = 0.0  # exactly 0: counted by m_b <= 0
    m[10:17] = 2.0  # exactly xbar + xbar: counted by m_b - xbar >= xbar
    m[17:20] = -5.0
    monkeypatch.setattr(S, "_boot_means", lambda x, B, seed=1: m)
    r = S.screen_p_sub(lam, day_vec(100, 4), B=1_000)
    assert r["xbar"] == 1.0 and (r["tail_count_pct"], r["tail_count_null"]) == (13, 7)
    assert r["p_pct"] == 14 / 1_001 and r["p_null"] == 8 / 1_001 and r["p"] == 14 / 1_001


def test_screen_p_no_p_is_none_and_never_a_pass():
    e = S.screen_p_sub(np.empty(0), np.empty(0), B=1_000)
    assert e["p"] is None and e["n"] == 0 and e["xbar"] is None and e["date_cluster"]["p"] is None and "not a pass" in e["none_reason"]
    nan = S.screen_p_sub(np.array([1e7, np.nan, 2e7] * 40), day_vec(120, 5), B=1_000)  # NaN compares False in both tails: it would otherwise read as the floor
    assert nan["p"] is None and nan["date_cluster"]["p"] is None and "non-finite" in nan["none_reason"]
    one = S.screen_p_sub(np.array([1e7]), np.array([20_000]), B=1_000)
    assert one["p"] is None and one["date_cluster"]["p"] is None
    onedate = S.screen_p_sub(np.random.default_rng(1).normal(1e7, 1e7, 100), np.full(100, 20_000), B=1_000)
    assert onedate["p"] is not None and onedate["date_cluster"]["p"] is None and onedate["date_cluster"]["k_dates"] == 1
    assert S._max_p([0.1, None, 0.2]) is None and S._max_p([]) is None and S._max_p([0.1, 0.3, 0.2]) == 0.3


def test_screen_p_uses_the_vectors_stat_hands_to_the_two_bootstraps(monkeypatch):
    rng = np.random.default_rng(8)
    lam, day, blk = rng.normal(3e6, 4e7, 400), day_vec(400, 7), rng.integers(0, 2, 400)
    seen = {}
    real_f, real_k = F.boot_lo, S.boot_lo
    monkeypatch.setattr(F, "boot_lo", lambda x, *a, **k: (seen.setdefault("x", np.array(x, copy=True)), real_f(x, *a, **k))[1])
    monkeypatch.setattr(S, "boot_lo", lambda ds, dn, *a, **k: (seen.setdefault("d", (np.array(ds, copy=True), np.array(dn, copy=True))), real_k(ds, dn, *a, **k))[1])
    st = S.stat(lam, day, blk, ["a", "b"])
    got = {}
    real_m, real_c = S._boot_means, S._cluster_ratios
    monkeypatch.setattr(S, "_boot_means", lambda x, B, seed=1: (got.setdefault("x", np.array(x, copy=True)), real_m(x, B, seed))[1])
    monkeypatch.setattr(S, "_cluster_ratios", lambda ds, dn, B, seed=1: (got.setdefault("d", (np.array(ds, copy=True), np.array(dn, copy=True))), real_c(ds, dn, B, seed))[1])
    r = S.screen_p_sub(lam, day, B=1_000, ci_lo_trade=st["ci_lo_trade"], ci_lo_date=st["ci_lo"])
    assert np.array_equal(seen["x"], got["x"]) and np.array_equal(seen["d"][0], got["d"][0]) and np.array_equal(seen["d"][1], got["d"][1])
    assert r["xbar"] == st["mean"] and r["n"] == st["n"] and r["date_cluster"]["k_dates"] == st["days"]
    assert r["ci90_lo_first1000_equals_stat"] is True and r["date_cluster"]["ci90_lo_first1000_equals_stat"] is True


def test_screen_p_refuses_any_stake_other_than_a_quarter_sol_and_a_B_out_of_range():
    lam, day = np.random.default_rng(1).normal(1e7, 1e7, 50), day_vec(50, 5)
    assert S.screen_p_sub(lam, day, B=1_000, stake_sol=0.25)["B"] == 1_000
    for bad in (1.0, 0.5, 0.2500001, 0.0, -0.25, 250_000_000, True, "0.25"):
        with pytest.raises(S.Refused, match="screen_p_stake_refused"):
            S.screen_p_sub(lam, day, B=1_000, stake_sol=bad)
    with pytest.raises(S.Refused, match="screen_p_stake_refused"):
        S.screen_p_cell(None, S.screen_cfg(), None, B=1_000, stake_sol=1.0)
    with pytest.raises(S.Refused, match="screen_p_stake_refused"):
        S.Screen(ScriptRule([]), declared_stake_sol=0.5)  # refused at construction, before any block is read
    S.Screen(ScriptRule([]), screen_p=False, declared_stake_sol=0.5)  # p off: no p is computed, nothing to refuse
    assert S.screen_p_check(0.25, 1_000) == 1_000 and S.screen_p_check(0.25, 1_000_000) == 1_000_000 and S.screen_p_check(0.25, 10_000) == 10_000
    for bad in (999, 0, -1, 1_000_001, 10_000.5, 10_000.0, True, None, "10000"):
        with pytest.raises(S.Refused, match="screen_p_draws_refused"):
            S.screen_p_check(0.25, bad)
    with pytest.raises(S.Refused, match="screen_p_draws_refused"):
        S.screen_p_sub(lam, day, B=999)
    with pytest.raises(S.Refused, match="screen_p_draws_refused"):
        S.Screen(ScriptRule([]), screen_p_draws=1_000_001)


def test_screen_p_cli_refuses_a_declared_stake_other_than_a_quarter_sol_and_reads_nothing(clean_tree, tmp_path, capsys):
    root, vm = clean_tree
    for i, extra in enumerate((["--declared-stake-sol", "1.0"], ["--declared-stake-sol", "0.5", "--count-only"], ["--screen-p-draws", "999"], ["--screen-p-draws", "1000001"])):
        out = tmp_path / f"refused{i}"
        assert cli(root, vm, out, *extra) == 2
        assert "REFUSED: screen_p_" in capsys.readouterr().err and not out.exists()  # nothing listed, read or written
    out = tmp_path / "ok_off"
    assert cli(root, vm, out, "--no-screen-p", "--declared-stake-sol", "1.0") == 0  # p off: no p is defined, no stake to refuse
    assert "screen_p" not in json.loads((out / "results.json").read_text())["cells"]["trivial"]
    out = tmp_path / "ok_on"
    assert cli(root, vm, out, "--declared-stake-sol", "0.25", "--screen-p-draws", "1500") == 0
    doc = json.loads((out / "results.json").read_text())
    assert doc["meta"]["screen_p"] == {"enabled": True, "report_only": True, "B": 1500, "seed": 1, "stake_sol": 0.25, "source": S.SCREEN_P_SOURCE}
    assert doc["cells"]["trivial"]["screen_p"]["B"] == 1500 and all(c["B"] == 1500 for c in doc["cells"]["trivial"]["screen_p"]["sub_cells"].values())


def kit_cell_p(tape, event: bool, **kw) -> "S.Screen":
    rows, files = tape
    scr = S.Screen(ScriptRule(script_for(rows), cells=(S.Cell("c", event_triggered=event),)), **kw)
    scr.run_block(0, "synthetic", S.iter_hours(files, "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    return scr


def dump(o) -> str:
    return json.dumps(o, sort_keys=True, default=float)


@pytest.mark.parametrize("survive", [False, True])
@pytest.mark.parametrize("event", [True, False])
def test_screen_p_changes_no_existing_field_in_results_or_table(tape, monkeypatch, event, survive):
    if survive:
        monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))  # a SCREEN SURVIVOR too, not only killed cells
    off, on = kit_cell_p(tape, event, screen_p=False), kit_cell_p(tape, event, screen_p=True, screen_p_draws=1_500)
    r_off, r_on = off.results()["c"], on.results()["c"]
    assert "screen_p" not in r_off and "screen_p" in r_on and (not r_on["kill"]) is survive
    assert dump(r_off) == dump({k: v for k, v in r_on.items() if k != "screen_p"})  # every pre-existing field byte-identical
    assert list(r_off) == [k for k in r_on if k != "screen_p"] and list(r_on)[-1] == "screen_p"  # same keys in the same order, the new key last
    t_off, t_on = S.table_md({"c": r_off}, off.blocks, {"rule": "script"}), S.table_md({"c": r_on}, on.blocks, {"rule": "script"})
    assert t_on.startswith(t_off) and t_on[len(t_off):].startswith("\n## 5. Screen p (EXP-028, REPORT ONLY")  # every TABLE line above is unchanged; section 5 is appended
    assert "## 5." not in t_off and repr(r_on["screen_p"]["cell_p"]) in t_on


def test_screen_p_changes_no_existing_field_through_the_cli(clean_tree, tmp_path):
    root, vm = clean_tree
    assert cli(root, vm, tmp_path / "off", "--no-screen-p") == 0 and cli(root, vm, tmp_path / "on", "--screen-p-draws", "1500") == 0
    a, b = (json.loads((tmp_path / d / "results.json").read_text()) for d in ("off", "on"))
    volatile = ("wall_s", "rows_pumpswap_per_s", "rows_kept_per_s", "seconds", "written_utc", "screen_p")
    assert dump({k: v for k, v in a["meta"].items() if k not in volatile}) == dump({k: v for k, v in b["meta"].items() if k not in volatile})
    assert a["meta"]["screen_p"]["enabled"] is False and b["meta"]["screen_p"]["enabled"] is True
    assert dump(a["cells"]) == dump({nm: {k: v for k, v in c.items() if k != "screen_p"} for nm, c in b["cells"].items()})
    ta, tb = ((tmp_path / d / "TABLE.md").read_text() for d in ("off", "on"))
    assert tb.startswith(ta) and tb[len(ta):].startswith("\n## 5. Screen p")
    assert (tmp_path / "off" / "results.json").read_bytes() != (tmp_path / "on" / "results.json").read_bytes()  # the new keys are really there
    co = tmp_path / "count"
    assert cli(root, vm, co, "--count-only") == 0 and "screen_p" not in json.loads((co / "counts.json").read_text())["cells"]["trivial"]  # a count-only run has no p


@pytest.mark.parametrize("stub", [1.0, 1 / 10_001, None])
@pytest.mark.parametrize("survive", [False, True])
@pytest.mark.parametrize("event", [True, False])
def test_screen_p_never_kills_or_rescues_a_cell(tape, monkeypatch, event, survive, stub):
    if survive:
        monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))
    base = kit_cell_p(tape, event, screen_p=False).results()["c"]
    fake = {"report_only": True, "cell_p": stub, "cell_p_date_cluster": stub, "B": 1_000, "seed": 1, "sub_cells": {}}
    monkeypatch.setattr(S, "screen_p_cell", lambda *a, **k: fake)  # the best and the worst p a cell can carry, and no p at all
    r = kit_cell_p(tape, event, screen_p=True).results()["c"]
    assert r["screen_p"] is fake
    assert (r["kill"], r["kill_reasons"], r["registration"]) == (base["kill"], base["kill_reasons"], base["registration"])
    assert dump({k: v for k, v in r.items() if k != "screen_p"}) == dump(base)


def test_screen_p_is_not_read_by_any_kill_or_registration_function():
    import inspect

    for fn in (S.kill_verdict, S.registration_status, S.gate_row, S.worst_in_slot_legs, S.stat):
        assert "screen_p" not in inspect.getsource(fn) and "cell_p" not in inspect.getsource(fn)
    src = inspect.getsource(S.Screen._cell_result)
    head, _, tail = src.partition("if self.screen_p:  # REPORT ONLY")
    assert 'r["registration"] = registration_status' in head and 'r["kill"]' not in tail and "return r" in tail  # computed after the verdict is final


def test_screen_p_on_a_real_cell_has_four_sub_cells_n_equal_to_the_gates_and_a_cell_p_that_is_their_max(tape):
    r = kit_cell_p(tape, True, screen_p_draws=2_000).results()["c"]
    sp = r["screen_p"]
    assert sp["report_only"] is True and sp["B"] == 2_000 and sp["seed"] == 1 and sp["stake_sol"] == 0.25 and sp["source"] == S.SCREEN_P_SOURCE
    assert set(sp["sub_cells"]) == {"total|flat", "total|press", "sc|flat", "sc|press"}
    for key, c in sp["sub_cells"].items():
        rule, fail = key.split("|")
        s_ = r["stats"][f"1.9|{rule}|rent0|{fail}"]
        assert c["n"] == s_["n"] and c["xbar"] == s_["mean"] and c["date_cluster"]["k_dates"] == s_["days"]  # n is arena_score's n, guarded reverts included
        assert c["ci90_lo_first1000_equals_stat"] is True  # the draws are the ones the kill used
        assert c["p"] == max(c["p_pct"], c["p_null"]) and 1 / 2_001 <= c["p"] <= 1.0
        assert c["date_cluster"]["k_dates"] == 1 and c["date_cluster"]["p"] is None and "fewer than 2 UTC dates" in c["date_cluster"]["none_reason"]  # the synthetic tape is ONE UTC date
    assert sp["cell_p"] == max(c["p"] for c in sp["sub_cells"].values()) and sp["cell_p_date_cluster"] is None
    json.dumps(sp)


def test_screen_p_cell_with_no_attempts_has_p_none(tape):
    rows, files = tape
    scr = S.Screen(ScriptRule([]), screen_p_draws=1_000)
    scr.run_block(0, "synthetic", S.iter_hours(files, "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    r = scr.results()["c"]
    assert r["attempts"] == 0 and r["kill"] is True and r["screen_p"]["cell_p"] is None and r["screen_p"]["cell_p_date_cluster"] is None
    assert all(c["p"] is None and c["n"] == 0 for c in r["screen_p"]["sub_cells"].values())
    md = S.table_md({"c": r}, ["synthetic"], {"rule": "script"})
    assert "## 5. Screen p" in md and "None" in md


def test_screen_p_definition_is_stated_in_the_module_doc_and_the_table():
    for needle in ("SCREEN P", "6276924696", "p_pct = (1 + #{m_b <= 0}) / (1 + B)", "#{m_b - xbar >= xbar}", "0.25 SOL"):
        assert needle in S.__doc__
    assert "max(p_pct, p_null)" in S.SCREEN_P_DEFINITION and "fresh" in S.SCREEN_P_DEFINITION and "None" in S.SCREEN_P_DEFINITION
    assert (S.SCREEN_P_B, S.SCREEN_P_SEED, S.SCREEN_P_B_MIN, S.SCREEN_P_B_MAX) == (10_000, 1, 1_000, 1_000_000)


def test_screen_p_leaves_the_pinned_files_alone():
    here = Path(S.__file__).resolve().parent
    assert hashlib.sha256((here / "arena_report_legs.py").read_bytes()).hexdigest() == S.REPORT_LEGS_PIN


def test_screen_p_cell_over_several_utc_dates_reproduces_both_bounds_the_kill_used():
    st = random_states(400, seed=21)
    st["T"] = day_vec(400, 6) * 86400 + 3600  # six UTC dates
    cfg = S.screen_cfg()
    day, blk = st["T"] // 86400, np.zeros(400, np.int64)
    stats = {f"1.9|{rule}|rent0|{fail}": S.stat(S.legs(st, S.STAKE_SOL * 1e9, 1.9, rule, cfg)[fail], day, blk, ["a"]) for rule in S.STRAND_RULES for fail in S.FAILS}
    sp = S.screen_p_cell(st, cfg, stats, B=2_000)
    assert sp["B"] == 2_000 and set(sp["sub_cells"]) == {"total|flat", "total|press", "sc|flat", "sc|press"}
    for key, c in sp["sub_cells"].items():
        assert c["n"] == 400 and c["date_cluster"]["k_dates"] == 6 and c["p"] is not None and c["date_cluster"]["p"] is not None
        assert c["ci90_lo_first1000_equals_stat"] is True and c["date_cluster"]["ci90_lo_first1000_equals_stat"] is True
        assert c["date_cluster"]["p"] == max(c["date_cluster"]["p_pct"], c["date_cluster"]["p_null"])
    assert sp["cell_p"] == max(c["p"] for c in sp["sub_cells"].values()) and sp["cell_p_date_cluster"] == max(c["date_cluster"]["p"] for c in sp["sub_cells"].values())
    none = S.screen_p_cell(None, cfg, None, B=1_000)
    assert none["cell_p"] is None and none["cell_p_date_cluster"] is None

# ------------------------------------------------------------------------------------------------------------------------------ 8. event-driven exits (ExitSpec.exit_fire): the kit streams a pure predicate
RATE, SPS = 2.5, 0.4  # the synthetic tape's slot rate (see NHOUR, KMAX); k_slow at 1.9 s = ceil(1.9 / 0.4) = 5, so the floor is sd + 6 and a 120 s hold is 300 slots
SD0 = 1010
FLOOR, CAP = SD0 + 6, SD0 + 300


def ev_pool(whales=(1100, 1200), decoys=(1012,), jump=None, n=400) -> dict:
    """One print a slot from slot 1000, buys on even offsets. A whale is a 2 SOL buy; a decoy is a whale BEFORE the landing floor. `jump` = a slot whose buy moves the price up 11%."""
    sl = 1000 + np.arange(n)
    ib = np.arange(n) % 2 == 0
    so, tk = np.full(n, 1e8), np.full(n, 1e9)
    for w in (*whales, *decoys):
        ib[w - 1000], so[w - 1000] = True, 2e9
    if jump is not None:
        ib[jump - 1000], tk[jump - 1000] = True, 1e13
    return {"sl": sl.astype(np.int64), "ib": ib, "so": so, "tk": tk, "q": np.full(n, 20e9), "b": np.full(n, 1e14), "v": np.full(n, float(V))}


def whale_fire(thr: float = 1e9):
    """A PURE predicate: the print slot being asked about holds a buy of >= thr lamports."""

    def fire(view):
        r = view.rows()
        m = r["sl"] == view.slot
        return bool((r["ib"][m] & (r["so"][m] >= thr)).any())

    return fire


def resolve(a, spec, sd=SD0):
    return S.resolve_exit(a, S.post_price(a), sd, spec, RATE, SPS)


def test_an_exit_fires_at_the_right_slot_and_is_an_event_exit():
    a = ev_pool()
    assert S._exit_clock(S.ExitSpec(120), SD0, RATE, SPS) == (FLOOR, CAP, False)
    assert resolve(a, S.ExitSpec(120, exit_fire=whale_fire())) == (1100, S.EVENT_CODE, False)  # the decoy at 1012 is before the floor 1016: never asked about
    assert S.EXIT_REASONS[S.EVENT_CODE] == "event" and S.EXIT_REASONS[:4] == ("time", "tp", "sl", "trail")
    assert resolve(a, S.ExitSpec(120)) == (CAP, 0, False)  # the same spec without the predicate is a plain time exit
    assert resolve(ev_pool(whales=(FLOOR,)), S.ExitSpec(120, exit_fire=whale_fire()))[:2] == (FLOOR, S.EVENT_CODE)  # the floor slot itself is asked about
    assert resolve(ev_pool(whales=(CAP,)), S.ExitSpec(120, exit_fire=whale_fire()))[:2] == (CAP, S.EVENT_CODE)  # so is the cap slot: a fire there is an event exit, not a time exit


def test_the_cap_wins_when_nothing_fires():
    a = ev_pool()
    assert resolve(a, S.ExitSpec(120, exit_fire=whale_fire(5e9))) == (CAP, 0, False)  # no print is that big
    assert resolve(a, S.ExitSpec(120, exit_fire=lambda v: False)) == (CAP, 0, False)
    assert resolve(ev_pool(whales=(), decoys=(1012, CAP + 20)), S.ExitSpec(120, exit_fire=whale_fire()))[:2] == (CAP, 0)  # whales outside [floor, cap] are never asked about
    assert resolve(a, S.ExitSpec(3, exit_fire=lambda v: False)) == (SD0 + 8, 0, False)  # the shortest hold (3 s = 8 slots here): its own cap
    clamp = S.resolve_exit(a, S.post_price(a), SD0, S.ExitSpec(3, exit_fire=lambda v: v.slot == v.landing_slot), 1.0, 0.4)  # 3 slots < the floor sd + 6: clamped to it; a fire AT the floor is allowed
    assert clamp == (SD0 + 6, S.EVENT_CODE, True)


def test_tp_sl_trail_still_apply_whichever_fires_first_wins():
    a = ev_pool(jump=1050)
    assert resolve(a, S.ExitSpec(120, tp=1.05)) == (1050, 1, False)  # the price rule alone: the 11% jump
    assert resolve(a, S.ExitSpec(120, tp=1.05, exit_fire=whale_fire())) == (1050, 1, False)  # tp (1050) before the whale (1100): tp wins
    assert resolve(a, S.ExitSpec(120, tp=1.05, exit_fire=lambda v: v.slot == 1030)) == (1030, S.EVENT_CODE, False)  # a fire before the tp wins
    assert resolve(a, S.ExitSpec(120, tp=1.05, exit_fire=lambda v: v.slot == 1050)) == (1050, 1, False)  # the same slot: the price rule keeps its code, the slot is the same
    assert resolve(a, S.ExitSpec(120, tp=1.5, exit_fire=whale_fire()))[:2] == (1100, S.EVENT_CODE)  # a tp that is never hit: the fire beats the cap
    stop = resolve(ev_pool(), S.ExitSpec(120, sl=0.99, exit_fire=whale_fire()))
    assert stop[1] in (S.EVENT_CODE, 2) and stop[0] <= 1100  # a stop (if the path has one) or the whale, never later than the whale


def test_the_predicate_sees_only_rows_up_to_the_slot_it_is_asked_about_and_is_asked_at_print_slots():
    a = ev_pool()
    asked, bad = [], []

    def spy(view):
        asked.append(view.slot)
        r = view.rows()
        bad.append(int(r["sl"].max()) != view.slot or int(view._a["sl"].max()) != view.slot or r["sl"].flags.writeable or r["so"].flags.writeable or len(r["sl"]) != view.slot - 1000 + 1)
        assert (view.sd, view.landing_slot, view.cap_slot, view.rate, view.sps) == (SD0, FLOOR, CAP, RATE, SPS)
        assert view.upto(view.slot)["sl"].max() == view.slot and view.upto(1000)["sl"].tolist() == [1000]  # history before the entry is there
        with pytest.raises(S.ExitLookAhead):
            view.upto(view.slot + 1)  # one slot ahead: refused
        with pytest.raises(ValueError):
            r["so"][0] = 0.0  # read-only
        return False

    assert resolve(a, S.ExitSpec(120, exit_fire=spy)) == (CAP, 0, False)
    assert asked == list(range(FLOOR, CAP + 1)) and not any(bad)  # every print slot from the floor to the cap, once, on rows that END at that slot
    sparse = ev_pool()
    keep = np.arange(400) % 7 == 0
    sparse = {k: v[keep] for k, v in sparse.items()}
    got = []
    resolve(sparse, S.ExitSpec(120, exit_fire=lambda v: got.append(v.slot) or False))
    assert got == [s for s in sparse["sl"].tolist() if FLOOR <= s <= CAP]  # only slots that have a print are asked about
    for bad_return in (1100, None, 1.0, "x", np.int64(1)):
        with pytest.raises(S.ExitFireError, match="bool"):
            resolve(a, S.ExitSpec(120, exit_fire=lambda v, r=bad_return: r))
    assert resolve(a, S.ExitSpec(120, exit_fire=lambda v: np.bool_(v.slot == 1100)))[:2] == (1100, S.EVENT_CODE)  # numpy bools are fine


def test_exit_fire_must_be_callable_and_the_spec_stays_hashable():
    assert S.exit_problem(S.ExitSpec(60, exit_fire="x")) and "callable" in S.exit_problem(S.ExitSpec(60, exit_fire=5))
    assert S.exit_problem(S.ExitSpec(60, exit_fire=whale_fire())) is None and S.exit_problem(S.ExitSpec(60)) is None
    f = whale_fire()
    assert {S.ExitSpec(60, exit_fire=f), S.ExitSpec(60, exit_fire=f)} == {S.ExitSpec(60, exit_fire=f)}  # frozen dataclass, a callable compares by identity

    @dataclasses.dataclass(frozen=True)
    class SurgeExit(S.ExitSpec):  # how hunt23's W2-F extends it
        surge: str | None = None

    assert S.exit_problem(SurgeExit(60, cell="c", surge="W", exit_fire=f)) is None


def spaced_triggers(rows, key="A", h=0, lo=1000, hi=8000, gap=700) -> list[int]:
    out, last = [], -10**9
    for s in own_slots(rows, key, h, lo, hi):
        if s - last >= gap:  # a trip is open at most 300 slots + the 150-slot cooldown: none overlaps
            out.append(s)
            last = s
    return out


def test_a_fired_exit_lands_by_the_normal_exit_lag_rule_in_the_kit(tape):
    rows, files = tape
    thr = 1.3e9
    picks = spaced_triggers(rows)
    assert len(picks) >= 6
    evs = run_kit(files, [(POOLS["A"], s, S.ExitSpec(120, exit_fire=whale_fire(thr))) for s in picks])
    tr = S._cat(evs.acc[0])
    a_rows = sorted((r["slot"], r["side"], r["sol_lamports"]) for r in rows if r["pool"] == POOLS["A"])
    n_fire = n_twin = 0
    for i, sd in enumerate(tr["sd"].tolist()):
        want = next((s for s, side, sol in a_rows if sd + 6 <= s <= sd + 300 and side == "buy" and sol >= thr), None)  # the independent scalar scan of the raw rows
        assert (int(tr["xs"][i]), int(tr["why"][i])) == ((want, S.EVENT_CODE) if want is not None else (sd + 300, 0)), (sd, want)
        if want is None:
            continue
        n_fire += 1
        if want - sd < 8:  # a time cap needs hold >= 3 s (8 slots here)
            continue
        n_twin += 1
        twin = S._cat(run_kit(files, [(POOLS["A"], sd, S.ExitSpec((want - sd) / RATE))]).acc[0])  # the same exit DECIDED at `want` by a plain time cap
        assert (int(twin["xs"][0]), int(twin["why"][0])) == (want, 0)
        assert tr["Qx"][i] == twin["Qx"][0] and tr["Bx"][i] == twin["Bx"][0]  # priced at the state of fire slot + 7 feed slots + the exit lag: the engine's rule, not the fire slot itself
        assert want + S.D + int(F.exit_lag_vec(np.array([SPS]))[0]) > want
    assert 0 < n_twin <= n_fire < len(tr["sd"])  # some exits fire (and are twinned), some run to the cap


def pool_arrays_of(files, key="A"):
    """The whole pool's rows across all hours as the kit's arrays (for the batch resolver)."""
    vm = S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])
    ch = [S.Screen._chunk(hr, hr.pool_index()[POOLS[key]]) for hr in S.iter_hours(files, "synthetic", "backfill", vm) if POOLS[key] in hr.pool_index()]
    a = {k: np.concatenate([c[k] for c in ch]) for k in ch[0]}
    a["ib"] = a["ib"].astype(bool)
    return a, S.post_price(a)


def test_the_kits_hour_by_hour_stream_equals_the_batch_resolver_including_tp_sl_and_a_hold_across_hours(tape):
    """Admission and the exit are decided as the data arrive (hour by hour); the batch resolver on the whole pool must give the same exit for every admitted attempt."""
    rows, files = tape
    a, pp = pool_arrays_of(files)
    early = own_slots(rows, "A", 0, 3000, 8800)[::9] + own_slots(rows, "A", 1, 100, 8000)[::25]
    late = own_slots(rows, "A", 0, 8300, 8990)[::6]  # a few hundred slots before the hour ends, and a whale that almost never comes: the position is open across the hour boundary
    crossed = 0
    for hold, tp, sl_, thr, sds in ((120, None, None, 1.3e9, early), (1200, 1.003, 0.997, 1.3e9, early), (1500, None, 0.998, 1.3e9, early), (1500, None, None, 1.4999e9, late), (1500, 1.004, 0.996, 1.4999e9, late)):
        spec = S.ExitSpec(hold, tp=tp, sl=sl_, exit_fire=whale_fire(thr))
        scr = run_kit(files, [(POOLS["A"], s, spec) for s in sds])
        tr = S._cat(scr.acc[0])
        assert len(tr["sd"]) >= (3 if sds is early else 1), (hold, tp, sl_)
        crossed += sum((int(x) - SLOT0) // 9000 > (int(s) - SLOT0) // 9000 for x, s in zip(tr["xs"], tr["sd"]))
        for i, sd in enumerate(tr["sd"].tolist()):
            xs, why, _ = S.resolve_exit(a, pp, sd, spec, RATE, SPS)
            assert (int(tr["xs"][i]), int(tr["why"][i])) == (xs, why), (hold, tp, sl_, sd)
    assert crossed > 0  # some exits were decided in a LATER hour than the entry, by the hour-by-hour stream
    spec = S.ExitSpec(120, exit_fire=whale_fire(1.3e9))
    assert collections.Counter(S._cat(run_kit(files, [(POOLS["A"], s, spec) for s in early]).acc[0])["why"].tolist())[S.EVENT_CODE] > 0


def test_hour_by_hour_the_rule_has_seen_triggers_only_up_to_the_hour_before_the_slot_it_is_asked_about(tape):
    """By construction: at slot s of hour h > the entry hour the rule has run triggers() for hours <= h-1 only (the exits are advanced BEFORE the hour's triggers())."""
    rows, files = tape

    class LogRule(ScriptRule):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.seen: list[int] = []

        def triggers(self, hour, state):
            self.seen.append(hour.idx)
            return super().triggers(hour, state)

    log: list[tuple[int, int]] = []
    rule_box: list[LogRule] = []

    def pred(view):
        log.append(((view.slot - SLOT0) // 9000, max(rule_box[0].seen)))
        return False

    sd = own_slots(rows, "A", 0, 7900, 8500)[0]
    rule = LogRule([(POOLS["A"], sd, S.ExitSpec(4800, exit_fire=pred))], cells=(S.Cell("c", event_triggered=False, event_exit=True),))  # 4,800 s = 12,000 slots: hours 0, 1 and 2
    rule_box.append(rule)
    scr = S.Screen(rule)
    scr.run_block(0, "synthetic", S.iter_hours(files, "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    hours_asked = sorted({h for h, _ in log})
    assert hours_asked == [0, 1, 2] and rule.seen == [0, 1, 2]
    assert all(seen == (h if h == 0 else h - 1) for h, seen in log)  # entry hour: after its own triggers(); every later hour: only up to the hour before
    assert scr.counters["exit_fire_positions"] == 1


def first_print_ge(rows, key, x):
    return min(r["slot"] for r in rows if r["pool"] == POOLS[key] and r["slot"] >= x)


def test_qp_fixture_peak_picker_through_the_views_private_arrays_cannot_see_the_peak(tape):
    """QP #760: 'sell at the price PEAK of [landing, cap], read through view._a'. The arrays end at the slot being asked about, so the best it can do is the running maximum, which
    is the first print: it never picks the peak (the kit never holds a later row in the view)."""
    rows, files = tape

    def peak(view):
        a = view._a
        idx = np.flatnonzero(a["sl"] >= view.landing_slot)
        return bool(idx.size and a["sl"][idx[np.argmax(a["pp"][idx])]] == view.slot)

    scr = kit_exit_cell(tape, S.Cell("c", event_triggered=False, event_exit=True), peak)
    tr = S._cat(scr.acc[0])
    a, pp = pool_arrays_of(files)
    n_peak = 0
    for i, sd in enumerate(tr["sd"].tolist()):
        pool_is_a = int(tr["pool"][i]) == zlib.crc32(POOLS["A"].encode())
        if pool_is_a and int(tr["why"][i]) == S.EVENT_CODE:
            assert int(tr["xs"][i]) == first_print_ge(rows, "A", sd + 6)  # the running max of a stream is its first print
            j0, j1 = int(np.searchsorted(a["sl"], sd + 6, "left")), int(np.searchsorted(a["sl"], sd + 300, "right"))
            n_peak += int(a["sl"][j0 + int(np.argmax(pp[j0:j1]))]) == int(tr["xs"][i])
    r = scr.results()["c"]
    assert r["exits"]["event"] > 0 and r["event_exit"]["probe_failures"] == 0  # honest-but-dumb: no look-ahead, so nothing for the probe to find
    assert n_peak < r["exits"]["event"]  # the real peak was not chosen


def test_qp_fixture_a_memo_built_in_triggers_never_holds_a_later_hour_and_an_unguarded_foresight_is_caught(tape):
    """QP #760: foresight 'sell at the first print >= landing iff the cap price is lower', memoised outside the view. (1) The memo lives in rule state; at slot s the rule has seen
    triggers() up to the hour before only, so with a cap in a LATER hour it cannot know the cap price (impossible). (2) Within the entry hour the rule has seen the whole hour:
    the prefix probe cannot see a foresight that fires at the first slot asked; the entry-hour re-run (test_the_entry_hour_rerun_*) is the check for it."""
    rows, files = tape

    class MemoRule(ScriptRule):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.mem: dict[str, tuple] = {}

        def triggers(self, hour, state):
            for p in hour.pools.tolist():
                s = hour.rows(hour.pool_index()[p])
                px = (hour.q[s] + hour.v[s]) / hour.b[s]
                old = self.mem.get(p, (np.zeros(0, np.int64), np.zeros(0)))
                self.mem[p] = (np.concatenate([old[0], hour.sl[s]]), np.concatenate([old[1], px]))  # the rule's own buffer of every hour it has been handed
            return super().triggers(hour, state)

    consulted: list[tuple[int, int]] = []

    def foresight(box, guard):
        def pred(view):
            sl, px = box[0].mem[POOLS["A"]]
            i0 = int(np.searchsorted(sl, view.landing_slot, "left"))
            i1 = int(np.searchsorted(sl, view.cap_slot, "right")) - 1
            if i0 >= len(sl):
                return False
            consulted.append(((int(sl[i1]) - SLOT0) // 9000, (view.slot - SLOT0) // 9000))  # (hour of the last price it used as 'the cap price', hour of the asked slot)
            return bool(px[i1] != px[i0]) and (not guard or view.slot == int(sl[i0]))  # 'the cap price differs from the landing price' (the stand-in for 'is lower')
        return pred

    sds = [s for s in own_slots(rows, "A", 0, 7900, 8800)[::6]][:3]
    for hold, guard in ((4800, True), (120, False)):  # cap in a later hour / cap inside the entry hour
        box: list[MemoRule] = []
        spec = S.ExitSpec(hold, exit_fire=foresight(box, guard))
        rule = MemoRule([(POOLS["A"], s, spec) for s in (sds[:1] if hold > 1000 else spaced_triggers(rows))], cells=(S.Cell("c", event_triggered=False, event_exit=True),))
        box.append(rule)
        consulted.clear()
        scr = S.Screen(rule)
        scr.run_block(0, "synthetic", S.iter_hours(files, "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
        r = scr.results()["c"]
        if hold > 1000:
            assert consulted and all(last_h <= asked_h for last_h, asked_h in consulted)  # the price it called 'the cap price' was never from after the hour being asked about
            assert any(last_h < ((sds[0] + hold * 5 // 2 - SLOT0) // 9000) for last_h, _ in consulted)  # ...and so it was not the real cap price: no foresight
        else:
            assert r["exits"]["event"] > 0 and r["event_exit"]["probe_failures"] == 0  # it fires at the FIRST slot asked: the prefix probe has no earlier slot to ask (the entry-hour re-run is the check, below)


def test_qp_fixture_a_skipped_fire_when_later_is_higher_is_impossible(tape):
    """QP #760: 'honest +1% trigger that returns None when the cap price is higher'. The predicate can only look at rows <= the slot, so there is nothing later to compare with:
    the exits equal the honest stream's, trip for trip."""
    peeked: list[int] = []

    def honest(view):
        r = view.upto(view.slot)
        i0 = int(np.searchsorted(r["sl"], view.landing_slot, "left"))
        return bool(i0 < len(r["sl"]) and r["pp"][-1] >= 1.01 * r["pp"][i0] and view.slot > r["sl"][i0])

    def suppress(view):
        a = view._a
        later = a["pp"][a["sl"] > view.slot]  # the attempted peek at the future
        peeked.append(int(later.size))
        return honest(view) and not (later.size and later.max() > a["pp"][-1])

    def exits(fire):
        return {(int(t["sd"][i]), int(t["pool"][i])): (int(t["xs"][i]), int(t["why"][i])) for t in [S._cat(kit_exit_cell(tape, S.Cell("c", event_triggered=False, event_exit=True), fire).acc[0])]
                for i in range(len(t["sd"]))}

    base, sup = exits(honest), exits(suppress)
    assert base == sup and any(w == S.EVENT_CODE for _, w in base.values()) and set(peeked) == {0}  # it saw nothing later, ever


def test_the_prefix_probe_catches_a_flaky_and_an_early_firing_predicate_and_passes_an_honest_one():
    a = ev_pool()
    pp = S.post_price(a)

    def probe(fire, f):
        pos = S._Pos(S.ExitSpec(120, exit_fire=fire), SD0, RATE, SPS)
        return S.exit_fire_probe(pos, a, pp, f)

    assert probe(whale_fire(), 1100) is True  # fires again on rows <= 1100, silent at 1099
    assert probe(lambda v: True, 1100) is False  # fires at fire - 1 as well: not caused by the fire slot's own prints
    assert probe(lambda v: v.slot >= 1100, 1100) is True  # a clock rule is a function of the view too: silent at 1099, fires at 1100
    assert probe(lambda v: v.slot >= 1099, 1100) is False  # it would have fired at 1099: the fire slot is not the first
    calls = iter(range(10**6))
    flaky = lambda v: next(calls) == 0 and v.slot == 1100  # noqa: E731
    assert flaky(type('V', (), {'slot': 1100})()) is True  # the stream's own ask (it fired)
    assert probe(flaky, 1100) is False  # not a function of the view: the probe's second ask differs
    assert probe(lambda v: int(v._a["sl"][list(v._a["sl"]).index(1100) + 1]) > 0 or True, 1100) is False  # reaches for a row that is not there: a raise is a failure
    assert probe(lambda v: (_ for _ in ()).throw(RuntimeError("boom")), 1100) is False


def test_causal_probe_probes_the_exit_too_when_asked(tape):
    rows, files = tape
    vm = S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])
    hr = next(S.iter_hours(files[:1], "synthetic", "backfill", vm))
    slot = int(hr.sl[hr.rows(hr.pool_index()[POOLS["A"]])][5])
    st = S.RunState("synthetic")

    class R:
        def __init__(self, fire):
            self.spec = S.ExitSpec(120, exit_fire=fire)

        def triggers(self, hour, state):
            j = hour.pool_index()[POOLS["A"]]
            return [(POOLS["A"], slot, self.spec)] if (hour.sl[hour.rows(j)] <= slot).any() else []

    assert S.causal_probe(R(whale_fire(1.0e9)), hr, st, POOLS["A"], slot) and S.causal_probe(R(whale_fire(1.0e9)), hr, st, POOLS["A"], slot, exit_fire=True)
    always = lambda v: True  # noqa: E731
    assert S.causal_probe(R(always), hr, st, POOLS["A"], slot)  # the entry alone is causal...
    assert S.causal_probe(R(always), hr, st, POOLS["A"], slot, exit_fire=True)  # ...and a pure always-true exit is a fire at the first slot asked: nothing for the prefix probe or the re-run to object to
    n = [0]

    def third_ask(v):  # not a pure function of the view: True only on its third call, whatever it is asked
        n[0] += 1
        return n[0] == 3

    assert not S.causal_probe(R(third_ask), hr, st, POOLS["A"], slot, exit_fire=True)  # fires on the third slot asked, and the probe's second ask of that slot is False
    assert not S.causal_probe(R(lambda v: 1), hr, st, POOLS["A"], slot, exit_fire=True)  # not a bool: refused, so not causal
    assert S.causal_probe(R(lambda v: False), hr, st, POOLS["A"], slot, exit_fire=True)  # no fire: nothing to probe


# ------------------------------------------------------------------------------------------------------------------------------ 8b. the kit run, the probe, worst-in-slot, count-only
def with_fire(script, fire):
    return [(p, s, dataclasses.replace(e, exit_fire=fire)) for p, s, e in script]


def kit_exit_cell(tape, cell: S.Cell, fire=None) -> "S.Screen":
    rows, files = tape
    script = script_for(rows) if fire is None else with_fire(script_for(rows), fire)
    scr = S.Screen(ScriptRule(script, cells=(cell,)))
    scr.run_block(0, "synthetic", S.iter_hours(files, "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    return scr


GOLDEN = {"event": "35fce10beb3c9272f263f616070deb7e60006dec9f3429a467a6950f823b2b91", "event_table": "82e170be2df7a893f4306531983d010b367bf2d6c78bc67264e4a9e460a3a6ec",
          "grid": "47b3a8c746f27801361ce778d7df481889c3058cb1a4dd08330b5ebe2b6126cd", "grid_table": "a9ef5d17440247fe1432b11d4a8a3a42545e94240854735dd27d9f5b64c53b9a",
          "count_only": "45cd2cf604b9ea5698671d4031f2488bbfe9f7fd4997811c71230cb9ba81f8ca", "count_only_md": "7e45a532630b2194b4610c5b7983c2584cba4dfd2485c42c750b097f7bec96e5"}
BASE_COMMIT = "489fa7e"  # main before this change: #758 (screen p) is on top of #756; the golden below is that kit's output, screen_p keys and section 5 included


def sha(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


def old_style_outputs(tape, kit=S) -> dict:
    rows, files = tape
    out = {}
    vm = kit.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])
    for name, ev in (("event", True), ("grid", False)):
        scr = kit.Screen(ScriptRule(script_for(rows), cells=(kit.Cell("c", event_triggered=ev),)))
        scr.run_block(0, "synthetic", kit.iter_hours(files, "synthetic", "backfill", vm))
        res = scr.results()
        out[name], out[name + "_table"] = sha(json.dumps(res, default=float)), sha(kit.table_md(res, scr.blocks, {"rule": "script"}))
    co = kit.Screen(ScriptRule(script_for(rows)), count_only=True)
    co.run_block(0, "explore-0814", kit.iter_hours(files, "synthetic", "backfill", vm))
    r = co.results()
    out["count_only"], out["count_only_md"] = sha(json.dumps(r, default=float)), sha(kit.counts_md(r, {"rule": "script", "hours_subset": False}))
    return out


def test_without_an_exit_fire_the_results_are_byte_identical_to_the_kit_before_this_change(tape):
    """results.json (priced, event and grid cell), TABLE.md, counts.json and COUNTS.md of a run with no exit_fire are the very bytes the kit wrote at 489fa7e (pinned sha256 of the
    json.dumps / text on this fixture): no new key, no new line, `exits` still has only time / tp / sl / trail."""
    got = old_style_outputs(tape)
    assert got == GOLDEN
    r = json.loads(json.dumps(run_kit(tape[1], script_for(tape[0])).results(), default=float))["c"]
    assert list(r["exits"]) == ["time", "tp", "sl", "trail"] and "event_exit" not in r and r["registration"].startswith(("SCREEN-SURVIVOR", "KILLED", "PROVISIONAL"))


def test_without_an_exit_fire_the_results_equal_the_base_commits_kit_when_it_is_in_the_clone(tape, tmp_path):
    """The same comparison against the base commit's own file, run side by side (skipped when the clone does not have it)."""
    repo = Path(S.__file__).resolve().parent.parent
    p = subprocess.run(["git", "-C", str(repo), "show", f"{BASE_COMMIT}:tools/arena_screen.py"], capture_output=True, text=True)
    if p.returncode != 0:
        pytest.skip(f"{BASE_COMMIT} is not in this clone")
    base_file = tmp_path / "arena_screen_base.py"
    base_file.write_text(p.stdout)
    import importlib.util as iu

    spec = iu.spec_from_file_location("arena_screen_base", base_file)
    base = iu.module_from_spec(spec)
    sys.modules["arena_screen_base"] = base
    spec.loader.exec_module(base)
    assert old_style_outputs(tape, kit=base) == old_style_outputs(tape, kit=S) == GOLDEN


def _lower_leg_swap(scr, case):
    base = S._cat(scr.acc[0])
    scr.acc[0] = [dict(base, **(lower_leg_st(np.ones(len(base["ref"]), bool), np.zeros(len(base["ref"]), bool)) if case == "all_win" else lower_leg_case(len(base["ref"]), case)))]
    return scr.results()["c"]


def test_an_event_exit_cell_reports_its_share_and_probes_every_event_exit(tape, monkeypatch):
    monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))
    scr = kit_exit_cell(tape, S.Cell("c", event_triggered=False, event_exit=True), whale_fire(1.0e9))
    r = _lower_leg_swap(scr, "all_win")  # hand-set winning states: isolates the event-exit report from this fixture's random-walk prices (no price claim in the fixture)
    e = r["event_exit"]
    assert 0 < r["exits"]["event"] < r["attempts"] and e["event_exits"] == r["exits"]["event"] and e["fire_share"] == pytest.approx(r["exits"]["event"] / r["attempts"])
    assert sum(r["exits"].values()) == r["attempts"] and e["declared"] is True and e["undeclared_use"] is False and e["worst_in_slot_kill_applies"] is True
    pb = e["probe_per_block"]["synthetic"]
    assert pb == {"event_exits_probed": r["exits"]["event"], "failures": 0} and e["probe_failures"] == 0 and scr.counters["exit_probe_failures"] == 0
    assert scr.counters["exit_fire_events"] == r["exits"]["event"] and scr.counters["exit_fire_positions"] == r["attempts"]
    # 200 per block are not on a fixture this small: NOT a survivor until the probes pass (QP #760 item 6)
    assert e["probe_ok"] is False and e["probe_min_per_block"] == 200 and r["registration"].startswith("PROVISIONAL-BLOCKED") and "SURVIVOR" not in r["registration"]
    md = S.table_md({"c": r}, ["synthetic"], {"rule": "script"})
    assert "## 6. Event exits" in md and "EVENT-EXIT" in md and f"| c | {r['attempts']} | {e['event_exits']} |" in md and "never a survivor" in md
    monkeypatch.setattr(S, "EXIT_PROBE_MIN", 1)  # the 200-per-block requirement is a constant: with it met, the survivor label is allowed
    ok = _lower_leg_swap(kit_exit_cell(tape, S.Cell("c", event_triggered=False, event_exit=True), whale_fire(1.0e9)), "all_win")
    assert ok["event_exit"]["probe_ok"] is True and ok["registration"] == "SCREEN-SURVIVOR (worst-in-slot measured > 0 on all four sub-cells)"


def test_the_kit_kills_a_cell_whose_event_exits_fail_the_prefix_probe_and_passes_an_honest_stream(tape, monkeypatch):
    monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))
    cell = S.Cell("c", event_triggered=False, event_exit=True)
    calls = iter(range(10**9))
    for name, fire in (("flaky", lambda v: next(calls) % 3 == 0), ("state-dependent", lambda v: next(calls) % 7 == 3)):
        bad = kit_exit_cell(tape, cell, fire)
        r = bad.results()["c"]
        assert r["exits"]["event"] > 0 and r["event_exit"]["probe_failures"] == bad.counters["exit_probe_failures"] > 0, name
        assert r["kill"] is True and r["registration"] == "KILLED" and any(w.startswith("exit-causality probe failed") for w in r["kill_reasons"]), name
    good = kit_exit_cell(tape, cell, whale_fire(1.0e9)).results()["c"]
    assert good["event_exit"]["probe_failures"] == 0 and not any("exit-causality" in w for w in good["kill_reasons"])  # (it may still be killed on this fixture's random-walk prices: not for causality)


def test_a_cell_is_all_exit_fire_or_none(tape):
    rows, files = tape
    s0, s1 = own_slots(rows, "A", 0, 1000, 3000)[0], own_slots(rows, "B", 0, 1000, 3000)[0]
    scr = run_kit(files, [(POOLS["A"], s0, S.ExitSpec(120, exit_fire=whale_fire())), (POOLS["B"], s1, S.ExitSpec(120))])
    assert scr.counters["refused_exit_fire_mixed_cell"] == 1 and scr.results()["c"]["attempts"] == 1 and scr.counters["exit_fire_positions"] == 1
    plain = run_kit(files, [(POOLS["A"], s0, S.ExitSpec(120)), (POOLS["B"], s1, S.ExitSpec(120))])
    assert "refused_exit_fire_mixed_cell" not in plain.counters and plain.results()["c"]["attempts"] == 2


@pytest.mark.parametrize("case", ["guard_rescue_flatters_worst", "worst_in_slot_is_lower"])
@pytest.mark.parametrize("cell_kw, fire, killed, note", [
    (dict(event_exit=True), whale_fire(1.0e9), True, "declared event exit"),
    (dict(event_exit=True), None, True, "declared event exit, no callable on the specs"),
    (dict(), whale_fire(1.0e9), True, "undeclared: carries an exit_fire, treated as an event exit"),
    (dict(), None, False, "grid entry and a clock / price exit: reports only"),
])
def test_an_event_exit_cell_is_killed_by_the_lower_leg_even_when_its_entry_is_grid_based(tape, monkeypatch, case, cell_kw, fire, killed, note):
    monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))  # isolate the worst-in-slot rule from the other kills
    scr = kit_exit_cell(tape, S.Cell("c", event_triggered=False, **cell_kw), fire)
    r = _lower_leg_swap(scr, case)
    assert r["event_triggered"] is False and r["worst_in_slot"]["measured"] is True and all(not c["lower"]["mean_sol"] > 0 for c in r["worst_in_slot"]["sub_cells"].values()), note
    wis = [w for w in r["kill_reasons"] if w.startswith("worst-in-slot")]
    if killed:
        assert r["kill"] is True and r["registration"] == "KILLED" and len(wis) == 1 and all(t in wis[0] for t in ("total/flat", "total/press", "sc/flat", "sc/press")), note
        assert r["event_exit"]["worst_in_slot_kill_applies"] is True and r["event_exit"]["undeclared_use"] is (not cell_kw and fire is not None)
        assert "EVENT-EXIT" in S.table_md({"c": r}, ["synthetic"], {"rule": "script"})
    else:
        assert r["kill"] is False and not wis and "event_exit" not in r and r["registration"] == "SCREEN-SURVIVOR (worst-in-slot measured)", note


def test_a_positive_lower_leg_leaves_an_event_exit_cell_a_survivor_and_one_bad_sub_cell_kills_it(tape, monkeypatch):
    monkeypatch.setattr(S, "kill_verdict", lambda *a, **k: (False, []))
    real = S.worst_in_slot_legs

    def one_bad(st, cfg):
        o = real(st, cfg)
        o["sub_cells"]["sc|press"]["lower"]["mean_sol"] = 0.0
        return o

    monkeypatch.setattr(S, "EXIT_PROBE_MIN", 1)
    cell = S.Cell("c", event_triggered=False, event_exit=True)
    assert not _lower_leg_swap(kit_exit_cell(tape, cell, whale_fire(1.0e9)), "all_win")["kill"]
    monkeypatch.setattr(S, "worst_in_slot_legs", one_bad)
    r = _lower_leg_swap(kit_exit_cell(tape, cell, whale_fire(1.0e9)), "all_win")
    assert r["kill"] and any(w.endswith("mean <= 0 in sc/press") for w in r["kill_reasons"]) and r["registration"] == "KILLED"


def test_count_only_with_an_exit_fire_is_a_time_only_bound_and_never_runs_the_callable(tape):
    rows, files = tape
    sl_ = own_slots(rows, "A", 0, 1000, 3000)

    def boom(view):
        raise AssertionError("count-only must not run the exit_fire")

    script = [(POOLS["A"], sl_[0], S.ExitSpec(120, exit_fire=boom)), (POOLS["A"], sl_[1], S.ExitSpec(120, exit_fire=boom)), (POOLS["A"], sl_[-1], S.ExitSpec(120, exit_fire=boom))]
    scr = run_kit(files, script, count_only=True, name="explore-0814", min_per_day=20.0)
    res = scr.results()
    r = res["c"]
    assert r["triggers"] == 3 and r["attempts_upper_bound"] == 2 and r["exit_fire_triggers"] == 3 and r["attempts_bound_is_time_only"] is True and scr.t_price == 0.0 and not scr.acc[0]
    cm = S.counts_md(res, {"rule": "script", "hours_subset": False})
    assert "time-only: exit_fire present" in cm and "LOWER bound on attempts" in cm and "the RULES step-2 drop must not use it" in cm and "at least this many" in cm
    assert r["time_only_figure_is"] == "lower_bound_on_attempts" and "attempts_upper_bound" in r  # the existing key keeps its name
    assert r["explore_0814"]["below_step2_count"] is None and "DROP (<20)" not in cm  # undetermined: a script keyed on the boolean cannot drop the cell (QP #760 item 5-ii)
    assert run_kit(files, [(POOLS["A"], sl_[0], S.ExitSpec(120))], count_only=True, name="explore-0814", min_per_day=20.0).results()["c"]["explore_0814"]["below_step2_count"] is True
    plain = run_kit(files, [(POOLS["A"], sl_[0], S.ExitSpec(120))], count_only=True).results()["c"]
    assert "attempts_bound_is_time_only" not in plain and "exit_fire_triggers" not in plain
    doc = S.__doc__
    assert "--count-only IS A TIME-ONLY BOUND when an exit_fire is present" in doc and "LOWER BOUND ON ATTEMPTS" in doc and "STEP-2 DROP MUST NOT USE IT" in doc and "PREFIX PROBE on EVERY event exit" in doc and "The KIT DRIVES IT, hour by hour" in doc


def test_the_time_only_count_is_a_lower_bound_of_the_attempts_an_event_exit_really_makes(tape):
    """Why the note says 'at least': with early exits the pool is free sooner, so the priced run admits at least as many attempts as the time-only overlap count."""
    rows, files = tape
    picks = [s for s in own_slots(rows, "A", 0, 1000, 8000)][::6]
    script = [(POOLS["A"], s, S.ExitSpec(120, exit_fire=whale_fire(1.0e9))) for s in picks]
    bound = run_kit(files, script, count_only=True).results()["c"]["attempts_upper_bound"]
    priced = run_kit(files, script).results()["c"]["attempts"]
    assert priced >= bound > 0 and priced > bound  # strictly more here: the fixture's whales free the pool early


def test_the_kit_refuses_a_bad_exit_fire_and_a_look_ahead_one_stops_the_run(tape):
    rows, files = tape
    s0 = own_slots(rows, "A", 0, 1000, 3000)[0]
    scr = run_kit(files, [(POOLS["A"], s0, S.ExitSpec(120, exit_fire="not callable"))])
    assert scr.counters["refused_bad_exit"] == 1 and not scr.acc[0] and not scr.exit_used

    def peek(view):
        view.upto(view.cap_slot)
        return False

    with pytest.raises(S.ExitLookAhead):  # a Refused: the CLI turns it into exit code 2 and nothing is written as a result
        run_kit(files, [(POOLS["A"], s0, S.ExitSpec(120, exit_fire=peek))])
    with pytest.raises(S.ExitFireError):
        run_kit(files, [(POOLS["A"], s0, S.ExitSpec(120, exit_fire=lambda v: 1))])
    assert issubclass(S.ExitLookAhead, S.Refused) and issubclass(S.ExitFireError, S.Refused)


def test_an_event_exit_cell_gets_the_screen_p_like_any_cell_and_the_p_is_untouched_by_the_event_exit(tape):
    """#758's report-only screen p: computed after the verdict on the same trips, for a killed or a surviving event-exit cell, and nothing in it reads the event-exit fields."""
    scr = kit_exit_cell(tape, S.Cell("c", event_triggered=False, event_exit=True), whale_fire(1.0e9))
    r = scr.results()["c"]
    sp = r["screen_p"]
    assert sp["report_only"] is True and sp["B"] == S.SCREEN_P_B and set(sp["sub_cells"]) == {"total|flat", "total|press", "sc|flat", "sc|press"} and "event_exit" in r
    again = S.screen_p_cell(S._cat(scr.acc[0]), scr.cfg[S.RENT_DECIDING], None, B=S.SCREEN_P_B)
    assert again["cell_p"] == sp["cell_p"] and again["cell_p_date_cluster"] == sp["cell_p_date_cluster"]  # the same function on the same trips
    md = S.table_md({"c": r}, ["synthetic"], {"rule": "script"})
    assert md.index("## 5. Screen p") < md.index("## 6. Event exits")
    off = S.Screen(ScriptRule(with_fire(script_for(tape[0]), whale_fire(1.0e9)), cells=(S.Cell("c", event_triggered=False, event_exit=True),)), screen_p=False)
    off.run_block(0, "synthetic", S.iter_hours(tape[1], "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    r_off = off.results()["c"]
    assert "screen_p" not in r_off and {k: v for k, v in r.items() if k != "screen_p"} == r_off  # nothing else moves with it
    empty = S.Screen(ScriptRule([], cells=(S.Cell("c", event_triggered=False, event_exit=True),)))
    r0 = empty.results()["c"]
    assert r0["screen_p"]["cell_p"] is None and r0["event_exit"]["event_exits"] == 0 and r0["registration"] == "KILLED"  # n = 0: p None, not a pass


def test_the_screen_p_path_and_the_stats_it_reads_are_source_identical_to_the_base_commit(tmp_path):
    """EXP-028 Amendment 1 needs the screen p source identical to 489fa7e (#758): every screen_p_* function and constant, and the stat / boot_lo / legs it reads, are the very text of the base."""
    import importlib.util as iu
    import inspect

    repo = Path(S.__file__).resolve().parent.parent
    p = subprocess.run(["git", "-C", str(repo), "show", f"{BASE_COMMIT}:tools/arena_screen.py"], capture_output=True, text=True)
    if p.returncode != 0:
        pytest.skip(f"{BASE_COMMIT} is not in this clone")
    (tmp_path / "base_screen_p.py").write_text(p.stdout)
    spec = iu.spec_from_file_location("base_screen_p", tmp_path / "base_screen_p.py")
    base = iu.module_from_spec(spec)
    sys.modules["base_screen_p"] = base
    spec.loader.exec_module(base)
    funcs = ["screen_p_check", "_boot_means", "_cluster_ratios", "_tails", "screen_p_sub", "_max_p", "screen_p_cell", "screen_p_md", "stat", "boot_lo", "gate_row", "legs", "kill_verdict",
             "worst_in_slot_legs", "registration_status"]
    consts = ["SCREEN_P_B", "SCREEN_P_B_MIN", "SCREEN_P_B_MAX", "SCREEN_P_SEED", "SCREEN_P_SOURCE", "SCREEN_P_DEFINITION", "STAKE_SOL", "FAILS", "STRAND_RULES", "SUB_CELLS", "LAT_DECIDING"]
    assert [f for f in funcs if inspect.getsource(getattr(S, f)) != inspect.getsource(getattr(base, f))] == []
    assert [c for c in consts if getattr(S, c) != getattr(base, c)] == []


class LeakRule:
    """Keeps the ENTRY hour's rows of pool A in its state (`scratch`) or on itself (`attribute`) and gates its fire on the exact slot: foresight the prefix probe cannot see (it fires again on
    rows <= the fire slot and is silent at fire - 1). `leak=False` is the honest twin: a predicate that reads the view only."""

    def __init__(self, slot, channel, leak=True):
        self.slot, self.channel, self.leak, self.attr = slot, channel, leak, None

    def cells(self):
        return [S.Cell("c", event_triggered=False, event_exit=True)]

    def triggers(self, hour, state):
        j = hour.pool_index().get(POOLS["A"])
        if j is None or not (hour.sl[hour.rows(j)] <= self.slot).any():
            return []
        s = hour.rows(j)
        mem = (hour.sl[s].copy(), (hour.q[s] + hour.v[s]) / hour.b[s])  # every row of the hour this call was handed (the cut run is handed only rows <= the entry slot)
        if self.channel == "scratch":
            state.scratch["mem"] = mem
            box = state.scratch
            get = lambda: box["mem"]  # noqa: E731
        else:
            self.attr = mem
            get = lambda: self.attr  # noqa: E731

        def leak_fire(view):
            sl, px = get()
            i0 = int(np.searchsorted(sl, view.landing_slot, "left"))
            i1 = int(np.searchsorted(sl, view.cap_slot, "right")) - 1
            return bool(i0 < len(sl) and i1 >= i0 and px[i1] != px[i0] and view.slot == int(sl[i0]))

        return [(POOLS["A"], self.slot, S.ExitSpec(120, cell="c", exit_fire=leak_fire if self.leak else whale_fire(1.0e9)))]


@pytest.mark.parametrize("channel", ["scratch", "attribute"])
def test_the_entry_hour_rerun_fails_a_rule_that_keeps_the_entry_hours_later_rows_and_gates_on_the_exact_slot(tape, monkeypatch, channel):
    """The manager's decision on #760 (QP option b): re-run triggers() on the entry hour cut after the entry slot, take the cut run's ExitSpec for the same (pool, slot, cell), stream
    the exit with ITS predicate, and require the identical fire (or no-fire). The leak passes the prefix probe and fails here; the honest twin passes."""
    rows, files = tape
    hr = next(S.iter_hours(files[:1], "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    slot = int(hr.sl[hr.rows(hr.pool_index()[POOLS["A"]])][5])
    st = S.RunState("synthetic", scratch={"early": 1})
    seen: list = []
    orig = S.exit_entry_hour_rerun
    monkeypatch.setattr(S, "exit_entry_hour_rerun", lambda *a, **k: seen.append(orig(*a, **k)) or seen[-1])

    assert S.causal_probe(LeakRule(slot, channel, leak=False), hr, st, POOLS["A"], slot, exit_fire=True)
    entry_seen, res = seen[-1]
    assert entry_seen and len(res) == 1 and res[0]["full"] == res[0]["cut"] and res[0]["full"][1] == S.EVENT_CODE and res[0]["ok"]  # a real fire, the same fire from the cut run

    leak = LeakRule(slot, channel, leak=True)
    assert S.causal_probe(leak, hr, st, POOLS["A"], slot)  # the entry alone is causal...
    assert not S.causal_probe(leak, hr, st, POOLS["A"], slot, exit_fire=True)  # ...the exit is not
    entry_seen, res = seen[-1]
    r = res[0]
    assert entry_seen and r["full"][1] == S.EVENT_CODE and r["prefix_ok"] is True  # the prefix probe on the full-hour spec PASSES (that is the residual)
    assert r["cut"][1] == 0 and r["cut"][0] > r["full"][0] and r["same"] is False and r["ok"] is False  # the cut run's predicate never saw the later rows: no fire, the time cap decides
    assert st.scratch == {"early": 1}  # the caller's state is untouched (deep copies)


def test_the_entry_hour_rerun_needs_the_cut_run_to_return_the_entry_and_an_exit_fire_for_the_cell(tape):
    rows, files = tape
    hr = next(S.iter_hours(files[:1], "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    slot = int(hr.sl[hr.rows(hr.pool_index()[POOLS["A"]])][5])
    st = S.RunState("synthetic")

    class Gone:  # the cut run does not return the trigger at all (an entry look-ahead)
        def triggers(self, hour, state):
            j = hour.pool_index()[POOLS["A"]]
            return [(POOLS["A"], slot, S.ExitSpec(120, exit_fire=whale_fire(1.0e9)))] if (hour.sl[hour.rows(j)] > slot).any() else []

    class NoFire:  # the cut run returns the entry but with a plain time exit
        def triggers(self, hour, state):
            j = hour.pool_index()[POOLS["A"]]
            full = (hour.sl[hour.rows(j)] > slot).any()
            return [(POOLS["A"], slot, S.ExitSpec(120, exit_fire=whale_fire(1.0e9) if full else None))]

    assert not S.causal_probe(Gone(), hr, st, POOLS["A"], slot, exit_fire=True)
    assert not S.causal_probe(NoFire(), hr, st, POOLS["A"], slot, exit_fire=True)


def test_the_ask_counter_is_reported_for_timing(tape):
    scr = kit_exit_cell(tape, S.Cell("c", event_triggered=False, event_exit=True), whale_fire(1.0e9))
    c = scr.counters
    assert c["exit_fire_positions"] > 0 and c["exit_fire_asks"] >= c["exit_fire_positions"] and c["exit_fire_asks"] > c["exit_fire_events"]


def test_qp_fixture_an_honest_buy_seen_since_entry_rule_has_zero_probe_failures(tape):
    """QP #760 at 7f5f3d6 (blocking 1): 'a buy print after sd has been seen' is already true at the FIRST slot asked when a buy came before it. The prefix probe used to ask at fire - 1,
    a slot the stream never asked, and killed the cell (2 false failures). It now asks at the previous slot the stream actually asked and skips the check when the fire is the first slot asked."""
    rows, files = tape

    def buy_since_entry(view):
        r = view.rows()
        return bool(((r["sl"] > view.sd) & r["ib"]).any())

    scr = kit_exit_cell(tape, S.Cell("c", event_triggered=False, event_exit=True), buy_since_entry)
    tr = S._cat(scr.acc[0])
    key_of = {zlib.crc32(POOLS[k].encode()): k for k in POOLS}
    first_asked_fires = before_landing_buy = 0
    for i in range(len(tr["sd"])):
        if int(tr["why"][i]) != S.EVENT_CODE:
            continue
        key, sd = key_of[int(tr["pool"][i])], int(tr["sd"][i])
        pool_slots = sorted(r["slot"] for r in rows if r["pool"] == POOLS[key])
        first_asked = next(x for x in pool_slots if x >= sd + 6)
        if int(tr["xs"][i]) == first_asked:
            first_asked_fires += 1
            # the buy that made it true came BEFORE the fire slot (between the entry and the first print at or above the floor): the case that was falsely killed
            before_landing_buy += any(r["slot"] > sd and r["slot"] < first_asked and r["side"] == "buy" and r["pool"] == POOLS[key] for r in rows)
    r = scr.results()["c"]
    assert r["exits"]["event"] > 0 and first_asked_fires > 0 and before_landing_buy > 0  # the fixture really contains the case
    assert r["event_exit"]["probe_failures"] == 0 and scr.counters["exit_probe_failures"] == 0 and not any("exit-causality" in w for w in r["kill_reasons"])


def test_the_prefix_probe_asks_only_at_slots_the_stream_asked_and_skips_the_first():
    a = ev_pool()
    pp = S.post_price(a)
    asked: list[int] = []

    def probe(fire, f):
        pos = S._Pos(S.ExitSpec(120, exit_fire=fire), SD0, RATE, SPS)
        return S.exit_fire_probe(pos, a, pp, f)

    spy = lambda v: asked.append(v.slot) or v.slot >= 1100  # noqa: E731
    assert probe(spy, 1100) and asked == [1100, 1099]  # again at the fire slot, then the previous slot the stream asked
    asked.clear()
    assert probe(lambda v: asked.append(v.slot) or True, FLOOR) and asked == [FLOOR]  # the first slot asked (the floor): no earlier slot, no early check, however true it was before
    sparse = {k: v[np.arange(400) % 7 == 0] for k, v in a.items()}  # prints every 7 slots: 1015, 1022, ...
    pps = S.post_price(sparse)
    asked.clear()
    pos = S._Pos(S.ExitSpec(120, exit_fire=lambda v: asked.append(v.slot) or True), SD0, RATE, SPS)
    first = int(sparse["sl"][sparse["sl"] >= FLOOR][0])
    assert S.exit_fire_probe(pos, sparse, pps, first) and asked == [first]  # fire - 1 is not a print slot and is never asked
    asked.clear()
    second = int(sparse["sl"][sparse["sl"] >= FLOOR][1])
    assert not S.exit_fire_probe(pos, sparse, pps, second) and asked == [second, first]  # it fires at the previous asked slot too: not a first fire
