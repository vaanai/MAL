"""tools/c1nf_parity.py on a small synthetic tape (no real tape, no network). Needs duckdb and pandas (skipped without them).

The multi-day replay (one pass, several graduation days) must write, for every day, the same npz as a one-day run of that day.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

duckdb = pytest.importorskip("duckdb")
pd = pytest.importorskip("pandas")

from tools import c1nf_features as cf  # noqa: E402
from tools import c1nf_parity as cp  # noqa: E402

V0 = 17_584_505_288
DAYS = ("2026-08-15", "2026-08-16")


def ep(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc).timestamp())


T_START = ep("2026-08-14T12:00")
SLOT0 = 300_000_000


def slot_of(bt: int, k: int) -> int:
    return SLOT0 + (bt - T_START) * 4 + k        # 4 slots per second; k < 4 orders prints within a second


def hour_of(bt: int) -> str:
    return dt.datetime.fromtimestamp(bt, dt.timezone.utc).strftime("%Y-%m-%dT%H")


def build_tape(root, seed: int = 7):
    rng = np.random.default_rng(seed)
    trades, creates, migs = [], [], []
    pools = [("MINT_A", "POOL_A", "CR1", "Alpha Dog", "ALP", ep("2026-08-15T03:00")),
             ("MINT_B", "POOL_B", "CR2", "dog rocket", "DOG", ep("2026-08-15T20:10")),
             ("MINT_C", "POOL_C", "CR1", "Gamma", "GAM", ep("2026-08-16T05:00")),
             ("MINT_D", "POOL_D", "CR3", "dog moon", "DGM", ep("2026-08-16T22:30"))]
    for mint, pool, cr, name, sym, g0 in pools:
        cbt = g0 - 900
        creates.append(dict(slot=slot_of(cbt, 0), mint=mint, creator=cr, name=name, symbol=sym, is_mayhem_mode=False, block_time=cbt, tx_index=0, event_index=0))
        for i in range(12):
            bt = cbt + 30 + i * 60
            trades.append(dict(venue="pump_bonding", mint=mint, trader=f"B{rng.integers(0, 8)}", side="buy", sol_lamports=int(rng.integers(1, 30)) * 10**8,
                               token_raw=10**12, quote_reserve=31 * 10**9, base_reserve=10**15, pool=None, slot=slot_of(bt, 1), block_time=bt, tx_index=1, event_index=0))
        migs.append(dict(slot=slot_of(g0, 0), mint=mint, block_time=g0, type="complete", tx_index=0, event_index=0))
        q, b = 85 * 10**9, 206 * 10**12
        t = g0 + 5
        end = g0 + 32 * 3600                       # past D+2 T01 for day-1 pools: the horizon cut is exercised
        n = 0
        while t < end:
            burst = ((t - g0) // 3600) % 3 == 0 and (t - g0) % 3600 < 900
            isb = bool(rng.random() < (0.7 if burst else 0.5))
            if isb:
                sol = int(rng.integers(5, 300)) * 10**7
                net = sol * (1 - cf.G_FEE)
                tok = int(b * net / (q + V0 + net))
                row_q, row_b = q, b
                q += int(net); b -= tok
            else:
                tok = int(rng.integers(1, 40)) * 10**11
                sol = int((q + V0) * tok / (b + tok))
                row_q, row_b = q, b
                q -= sol; b += tok
            trader = f"N{mint}{n}" if burst else f"W{rng.integers(0, 40)}"
            trades.append(dict(venue="pumpswap", mint=mint, trader=trader, side="buy" if isb else "sell", sol_lamports=sol, token_raw=tok, quote_reserve=row_q,
                               base_reserve=row_b, pool=pool, slot=slot_of(t, 2), block_time=t, tx_index=2, event_index=0))
            n += 1
            t += int(rng.integers(3, 12)) if burst else int(rng.integers(20, 140))
            if mint == "MINT_B" and g0 + 23 * 3600 + 1800 <= t < g0 + 31 * 3600:
                t = g0 + 31 * 3600                 # quiet until after D+2 T01: its last decisions' look-ahead print is past the batch horizon
    tr = pd.DataFrame(trades)
    cr = pd.DataFrame(creates)
    mg = pd.DataFrame(migs)
    for sub in ("trades", "creates", "migrations"):
        (root / "tape" / sub).mkdir(parents=True)
    con = duckdb.connect()
    hours = sorted({hour_of(int(x)) for x in range(T_START, ep("2026-08-18T02:00"), 3600)})
    for h in hours:
        lo = int(dt.datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc).timestamp())
        for sub, df in (("trades", tr), ("creates", cr), ("migrations", mg)):
            part = df[(df.block_time >= lo) & (df.block_time < lo + 3600)]
            con.register("part", part)
            con.execute(f"COPY (SELECT * FROM part) TO '{root}/tape/{sub}/{h}.parquet' (FORMAT parquet)")
            con.unregister("part")
    (root / "shared").mkdir()
    tok_df = pd.DataFrame({"pool": [p[1] for p in pools], "v0_lamports": [V0] * len(pools)})
    con.register("tok", tok_df)
    con.execute(f"COPY (SELECT * FROM tok) TO '{root}/shared/tokens.parquet' (FORMAT parquet)")
    (root / "verify" / "work").mkdir(parents=True)
    (root / "verify" / "wl").mkdir(parents=True)
    uni = pd.DataFrame({"mid": list(range(len(pools))), "mint": [p[0] for p in pools], "pool": [p[1] for p in pools], "v": [V0] * len(pools),
                        "g": [p[5] for p in pools], "gday": [cf.utc_day(p[5]) for p in pools], "ch": [p[2] for p in pools]})
    con.register("uni", uni)
    con.execute(f"COPY (SELECT * FROM uni) TO '{root}/verify/work/universe.parquet' (FORMAT parquet)")
    traders = sorted(set(tr.trader))
    ths = {x: con.execute("SELECT hash(?)", [x]).fetchone()[0] for x in traders}
    for i, day in enumerate(("2026-08-13", "2026-08-14", "2026-08-15", "2026-08-16")):
        sel = traders[i::3]
        wl = pd.DataFrame({"th": np.array([ths[x] for x in sel], dtype=np.uint64), "n": rng.integers(1, 900, len(sel)).astype(float),
                           "nbond": rng.integers(0, 50, len(sel)).astype(float), "cash": rng.normal(0, 2, len(sel)),
                           "nwin": rng.integers(0, 6, len(sel)).astype(float), "nrt": rng.integers(0, 9, len(sel)).astype(float),
                           "buy": rng.random(len(sel)) * 5})
        con.register("wl", wl)
        con.execute(f"COPY (SELECT * FROM wl) TO '{root}/verify/wl/{day}.parquet' (FORMAT parquet)")
        con.unregister("wl")
    (root / "scr").mkdir()


@pytest.fixture()
def tape(tmp_path, monkeypatch):
    build_tape(tmp_path)
    monkeypatch.setattr(cp, "TAPE", str(tmp_path / "tape"))
    monkeypatch.setattr(cp, "SH", str(tmp_path / "shared"))
    monkeypatch.setattr(cp, "VER", str(tmp_path / "verify"))
    monkeypatch.setattr(cp, "SCR", str(tmp_path / "scr"))
    return tmp_path


def _same(a, b):
    if a.dtype.kind == "f":
        return a.shape == b.shape and np.array_equal(a, b, equal_nan=True)
    return a.shape == b.shape and np.array_equal(a, b)


def test_multi_day_pass_equals_one_day_runs(tape):
    one = {}
    for d in DAYS:
        cp.replay(d, "2026-08-14T12", str(tape / f"one_{d}.npz"))
        one[d] = np.load(tape / f"one_{d}.npz", allow_pickle=False)
    cp.main(["replay", "--days", ",".join(DAYS), "--out-dir", str(tape / "multi")])
    for d in DAYS:
        A, B = one[d], np.load(tape / "multi" / f"replay_{d}.npz", allow_pickle=False)
        assert set(A.files) == set(B.files)
        for k in A.files:
            if k == "seconds":
                continue
            assert _same(A[k], B[k]), (d, k)
        assert len(A["eligible"]) == 2 and A["live_pre"].sum() > 0 and A["exact_pre"].sum() > 0
        assert np.isfinite(A["live_vec"][A["live_pre"]][:, cf.FIDX["wb5_n"]]).any()       # the ledger reached the wallet features
    # the two days hold different pools
    assert set(one[DAYS[0]]["eligible"]) == {"POOL_A", "POOL_B"} and set(one[DAYS[1]]["eligible"]) == {"POOL_C", "POOL_D"}


def test_cli_requires_matching_out_flag():
    with pytest.raises(SystemExit):
        cp.main(["replay", "--days", "2026-08-15"])
    with pytest.raises(SystemExit):
        cp.main(["replay", "--day", "2026-08-15"])
