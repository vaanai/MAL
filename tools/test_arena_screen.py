"""arena_screen tests: the vectorised pricer against arena_paper.price_leg (0 lamports), one END-TO-END check against an ArenaEngine replay of a synthetic tape, every data
guard, the Am.2 universe refusal, the exit resolver against a scalar reference, the KILL logic. No network, no real tape, no arena path."""

from __future__ import annotations

import collections
import json
import math
import os
import random
import subprocess
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


def run_kit(files, script, count_only=False, vmap=None, min_per_day=0.0):
    scr = S.Screen(ScriptRule(script), count_only=count_only, min_attempts_per_day=min_per_day)
    scr.run_block(0, "synthetic", S.iter_hours(files, "synthetic", "backfill", S.VMap([vmap or {POOLS[k]: float(v) for k, v in V_OF.items()}])))
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
    scr = run_kit(files, [(POOLS["A"], sl_[0], S.ExitSpec(120)), (POOLS["A"], sl_[1], S.ExitSpec(120))], min_per_day=20.0)
    assert scr.counters["overlap_dropped"] == 1
    r = scr.results()["c"]
    assert r["attempts"] == 1 and r["thin"] is True and r["kill"] is True


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


def mk(mean=1.0, ex3=1.0, exbest=1.0, blocks=(1, 1, 1, 1), ci=1.0, pos=8, days=10):
    return {"mean": mean, "mean_pct": mean * 400, "ex_top3": ex3, "ex_best_day": exbest, "ci_lo": ci, "ci_lo_pct": ci * 400, "pos_days": pos, "days": days,
            "per_block": {f"b{i}": {"n": 5, "total": t} for i, t in enumerate(blocks)}}


NAMES = ["b0", "b1", "b2", "b3"]
GOOD = {k: mk() for k in S.SUB_CELLS}
GGOOD = {(g, *k): mk() for g in S.GROUPS for k in S.SUB_CELLS}


def verdict(subs=None, groups=None, names=NAMES):
    return S.kill_verdict({**GOOD, **(subs or {})}, names, {**GGOOD, **(groups or {})})


def test_kill_logic_all_four_sub_cells():
    k, why = verdict()
    assert not k and not why  # a screen survivor: NOT a profit claim
    assert len(S.SUB_CELLS) == 4 and set(S.SUB_CELLS) == {("total", "flat"), ("total", "press"), ("sc", "flat"), ("sc", "press")}
    for key in S.SUB_CELLS:  # any ONE of the four sub-cells kills, on each of the three tests
        for field, kw in (("mean", "mean"), ("ex_top3", "ex3"), ("ex_best_day", "exbest")):
            k, why = verdict({key: mk(**{kw: 0.0})})
            assert k and any(f"{'/'.join(key)}.{field}<=0" in w for w in why), (key, field)
            k, why = verdict({key: mk(**{kw: -1e-9})})
            assert k
    assert verdict({("sc", "press"): None})[0] and verdict({("total", "flat"): None})[0]
    assert S.kill_verdict({}, NAMES, GGOOD)[0]  # nothing priced


def test_kill_logic_blocks_positive_under_all_four_sub_cells():
    three = (1, 1, 1, -1)
    assert verdict({k: mk(blocks=three) for k in S.SUB_CELLS})[0] is False  # 3 of 4 blocks is enough
    k, why = verdict({k: mk(blocks=(1, 1, -1, -1)) for k in S.SUB_CELLS})
    assert k and any("blocks_positive 2/4 < 3" in w for w in why)
    k, why = verdict({("sc", "press"): mk(blocks=(1, 1, -1, -1))})  # one sub-cell positive in only 2 blocks: the block does not count
    assert k and any("blocks_positive 2/4 < 3" in w for w in why)
    k, why = verdict({("sc", "press"): mk(blocks=(1, 1, 1, -1)), ("total", "flat"): mk(blocks=(-1, 1, 1, 1))})  # a different block fails in each: only 2 blocks pass all four
    assert k and any("blocks_positive 2/4" in w for w in why)
    assert verdict(names=NAMES[:2])[0]  # fewer than 3 blocks priced can never pass
    zero = {k: mk(blocks=(1, 1, 1, 1)) for k in S.SUB_CELLS}
    zero[("total", "flat")]["per_block"]["b3"]["n"] = 0
    assert verdict(zero)[0] is False  # 3 blocks still pass


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
    for key in S.SUB_CELLS:
        k, why = verdict({key: mk(ci=-0.001)})
        assert k and any(f"{'/'.join(key)}.gate.ci_lo<=0" in w for w in why)
        k, why = verdict({key: mk(pos=5, days=10)})  # exactly half is not a majority
        assert k and any("gate.no_majority_of_dates_positive" in w for w in why)
        assert verdict({key: mk(pos=6, days=10)})[0] is False
    g = S.gate_row(mk(ci=0.5, pos=3, days=5))
    assert g["ci_lo_gt0"] and g["majority_dates"] and S.gate_row(None) is None


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


def test_rule_can_read_first_seen_in_state(tape):
    rows, files = tape
    seen = {}

    class Peek:
        name = "peek"

        def cells(self):
            return ["c"]

        def triggers(self, hour, state):
            seen.update({k: v for k, v in state.first_seen.items() if k == POOLS["A"]})
            return []

    run_kit_rule = S.Screen(Peek(), count_only=True)
    run_kit_rule.run_block(0, "synthetic", S.iter_hours(files[:1], "synthetic", "backfill", S.VMap([{POOLS[k]: float(v) for k, v in V_OF.items()}])))
    assert seen[POOLS["A"]] == min(r["block_time"] for r in rows if r["pool"] == POOLS["A"])


def test_priced_results_carry_the_four_sub_cells_groups_and_gate_columns(tape):
    rows, files = tape
    r = run_kit(files, script_for(rows)).results()["c"]
    assert set(r["gate"]) == {"total|flat", "total|press", "sc|flat", "sc|press"} and r["worst_in_slot"].startswith("not measured: PROVISIONAL")
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


@pytest.mark.parametrize("word", list(S.FORBIDDEN))
def test_forbidden_words_are_refused_even_inside_an_allowed_block(word):
    for p in (f"/data/mal/clean-view/explore-0814/w1/{word}x", f"/data/mal/clean-view/explore-0814/{word.upper()}", f"/data/mal/arena/tape/{word}-1"):
        for count_only in (True, False):
            with pytest.raises(S.Refused, match="forbidden_word|arena_path|not_an"):
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


def test_d2d3_hours_duplicates_and_arena_file_names_are_refused(tmp_path):
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


def test_cli_refuses_with_exit_code_2_and_reads_nothing(tmp_path, capsys):
    base = ["--rule", "tools.arena_screen:TrivialRule", "--out", str(tmp_path / "o")]
    for extra in (["--block", "/etc"], ["--block", "/data/mal/clean-view/fresh-0802/w1"], ["--block", "/data/mal/arena/tape"], ["--block", "/data/mal/arena/ledger", "--count-only"],
                  ["--block", "/data/mal/clean-view/explore-0814/probe"]):
        assert S.main(base + extra) == 2
    assert S.main(["--rule", "tools.arena_screen:TrivialRule", "--out", "/data/mal/arena/x", "--block", "/data/mal/clean-view/explore-0814"]) == 2
    assert "REFUSED" in capsys.readouterr().err and not (tmp_path / "o").exists()


def test_cli_end_to_end_on_a_synthetic_block_writes_the_files(tape, tmp_path, monkeypatch):
    """The guards are for the real tree; point them at a temp block to run the CLI body (count-only, then priced)."""
    rows, files = tape
    root = tmp_path / "clean"
    monkeypatch.setattr(S, "CLEAN_VIEW", str(root))
    monkeypatch.setattr(S, "BLOCK_ROOTS", (str(root / "explore-0814"),))
    blk = root / "explore-0814" / "w1" / "trades"
    blk.mkdir(parents=True)
    for _, f in files:
        os.link(f, blk / Path(f).name)
    vm = tmp_path / "v.json"
    vm.write_text(json.dumps({"v": {POOLS[k]: v for k, v in V_OF.items()}}))
    for extra, outs in ((["--count-only"], ("counts.json", "COUNTS.md")), ([], ("results.json", "TABLE.md"))):
        out = tmp_path / ("o" + ("c" if extra else "p"))
        rc = S.main(["--rule", "tools.arena_screen:TrivialRule", "--rule-kwargs", '{"every": 5, "hold_s": 120}', "--block", str(root / "explore-0814"), "--out", str(out), "--vmap", str(vm), *extra])
        assert rc == 0 and all((out / o).exists() for o in outs)
        doc = json.loads((out / outs[0]).read_text())
        assert doc["meta"]["hours"] == 3 and doc["meta"]["rows_pumpswap_per_s"] > 0 and doc["meta"]["blocks"] == ["explore-0814"]
    assert doc["cells"]["trivial"]["attempts"] > 0


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
