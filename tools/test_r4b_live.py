"""Tests for tools/r4b_live.py. SYNTHETIC data only (tmp dirs); nothing here reads /data/mal or any tape.

The equivalence tests run the pinned batch path (tools/r4b_layout.build -> tools/r4b_read.build_rows -> tools/r4b_score.bag_score -> tools/r4b_book.xs_book,
exactly the calls of r4b_read.run_deciding) and the streaming engine on the same synthetic walker dir and require equal md5s of the scored decision rows
and of the book's trades. Needs duckdb (/data/mal/audit-1008/venv) and lightgbm 4.7.0 (/data/mal/venv):
  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_live.py
"""

from __future__ import annotations

import inspect
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

duckdb = pytest.importorskip("duckdb")

from tools import r4b_book as bk  # noqa: E402
from tools import r4b_frozen as FZ  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools import r4b_live as lv  # noqa: E402
from tools.test_r4b_layout import make_walk  # noqa: E402

H0, H1 = "2026-08-14T00", "2026-08-14T06"
BT0 = int(datetime(2026, 8, 14, tzinfo=timezone.utc).timestamp())
S = 438_000_000
V = 17_600_000_000
W0, E = BT0 + 2 * 3600, BT0 + 6 * 3600


def pda_of(mint: str) -> str:
    return "PDA_" + mint


def slot_of(bt: int) -> int:
    return S + (bt - BT0) * 5 // 2


def hour_of(bt: int) -> str:
    return datetime.fromtimestamp(bt, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def scenario(seed: int = 7, n_members: int = 16) -> dict:
    """Six hours of graduations and PumpSwap prints plus 2 extra trade hours. Members trade at different rates so the top 10 moves between marks."""
    rnd = random.Random(seed)
    hours = {lv.hour_key(datetime.fromtimestamp(BT0 + 3600 * i, tz=timezone.utc)): {"trades": [], "creates": [], "migrations": []} for i in range(8)}

    def put(sub, bt, row):
        hours[hour_of(bt)][sub].append(row)

    traders = [f"TR{i}" for i in range(40)]
    specs = [(f"M{i:02d}", "ok") for i in range(n_members)] + [("XMAY", "mayhem"), ("XNWS", "nonwsol"), ("XVHI", "vhigh"), ("XNOM", "nomig"),
                                                                ("XLATE", "late")]  # XLATE: a member whose first print is < 600 s before W0 (D3 drop)
    for k, (mint, kind) in enumerate(specs):
        bt_c = W0 - 650 if kind == "late" else BT0 + 1000 + 151 * k
        sc = slot_of(bt_c)
        creator = f"CR{k}"
        put("creates", bt_c - 900, {"type": "create", "mint": mint, "creator": creator, "is_mayhem_mode": kind == "mayhem", "slot": slot_of(bt_c - 900),
                                    "block_time": bt_c - 900})
        for j in range(5):  # bonding prints before the completion, the creator sells once
            btb = bt_c - 600 + 100 * j
            put("trades", btb, {"venue": "pump_bonding", "mint": mint, "trader": creator if j == 3 else rnd.choice(traders), "side": "sell" if j == 3 else "buy",
                                "sol_lamports": rnd.randint(10**8, 2 * 10**9), "token_raw": rnd.randint(10**10, 10**12), "quote_reserve": 30 * 10**9,
                                "base_reserve": 10**15, "pool": None, "slot": slot_of(btb), "signature": f"b{mint}{j}", "event_index": 0, "tx_index": j,
                                "block_time": btb})
        put("migrations", bt_c, {"type": "complete", "mint": mint, "slot": sc, "block_time": bt_c, "tx_index": 3, "event_index": 0})
        if kind != "nomig":
            put("migrations", bt_c, {"type": "migration", "mint": mint, "slot": sc, "block_time": bt_c, "tx_index": 3, "event_index": 1,
                                     "pool": pda_of(mint), "quote_mint": "NOT_WSOL" if kind == "nonwsol" else rl.WSOL_MINT})
        q, b = 85 * 10**9, 206 * 10**12
        vq = 17_800_000_000 if kind == "vhigh" else V
        rate = 8 if kind == "late" else 30 + 25 * (k % 7)
        bt, n = bt_c + (550 if kind == "late" else 3), 0
        while bt < BT0 + 8 * 3600 - 5:
            if n > 40 and k % 5 == 1 and bt > BT0 + 3 * 3600 and bt < BT0 + 4 * 3600:  # a quiet hour for some mints
                bt += 900
                continue
            buy = rnd.random() < (0.62 if (k + bt // 3600) % 3 == 0 else 0.45)
            sol = rnd.randint(5 * 10**7, 3 * 10**9)
            if buy:
                tok = int(b * sol / (q + vq + sol))
                row_q, row_b = q, b
                q, b = q + sol, b - tok
            else:
                tok = int(b * rnd.uniform(0.001, 0.03))
                sol = int((q + vq) * tok / (b + tok))
                if sol >= q:
                    bt += rate
                    continue
                row_q, row_b = q, b
                q, b = q - sol, b + tok
            txi = None if (n % 17 == 5) else rnd.randint(0, 900)
            put("trades", bt, {"venue": "pumpswap", "mint": mint, "trader": creator if n % 23 == 0 else rnd.choice(traders), "side": "buy" if buy else "sell",
                               "sol_lamports": sol, "token_raw": tok, "quote_reserve": row_q, "base_reserve": row_b, "pool": pda_of(mint), "slot": slot_of(bt),
                               "signature": f"s{mint}{n}", "event_index": n % 2, "tx_index": txi, "block_time": bt, "virtual_quote_reserves": vq + (n % 3)})
            n += 1
            bt += rnd.randint(max(2, rate // 3), rate * 2)
    for h in hours.values():  # rows of a hour file are in slot order, ties shuffled (the frn order is the file order)
        rnd.shuffle(h["trades"])
        h["trades"].sort(key=lambda r: r["slot"])
    return hours


@pytest.fixture(scope="module")
def walk(tmp_path_factory):
    root = tmp_path_factory.mktemp("r4blive") / "walk"
    make_walk(root, scenario())
    return root


def verify_table(root: Path) -> dict:
    return {r["hour"]: r for r in (json.loads(x) for x in (root / "verify.jsonl").read_text().splitlines() if x.strip())}


def batch_path(root: Path, scratch: Path, boosters, score_fn=None):
    """The pinned read's decision calls (r4b_read.run_deciding) on the synthetic dir: build, build_rows, bag_score, xs_book.
    score_fn(boosters, X) replaces bag_score when given (tie / ranking tests)."""
    from tools import r4b_read as rr
    from tools import r4b_score as rs

    rl.build(root, H0, H1, scratch, v_source="event", canonical_fn=pda_of, problems_fn=lambda *_: [], verify=verify_table(root), memory_limit="1GB", threads=2)
    plan = SimpleNamespace(w0=datetime.fromtimestamp(W0, tz=timezone.utc), e=datetime.fromtimestamp(E, tz=timezone.utc))
    deps = SimpleNamespace(threads=2, memory_limit="1GB")
    R, rep, con = rr.build_rows(scratch, plan, {"delta_slots": 0.0}, {"2026-08-14": 0.4}, deps, BT0)
    meta = {m: mi for m, mi in con.sql(f"select mid, mint from read_parquet('{scratch}/meta.parquet')").fetchall()}
    con.close()
    mid = np.concatenate(R.mid) if R.mid else np.zeros(0, np.int64)
    T = np.concatenate(R.T) if R.T else np.zeros(0, np.int64)
    sd = np.concatenate(R.sd) if R.sd else np.zeros(0, np.int64)
    X = np.vstack(R.X) if R.X else np.zeros((0, FZ.N_FEATURES), np.float32)
    score = (score_fn or rs.bag_score)(boosters, X) if len(X) else np.zeros(0)
    b = bk.xs_book(T, mid, score)
    trades = sorted(((meta[int(m)], int(t0), int(t1), str(r)) for m, t0, t1, r in zip(b["mid"], b["T0"], b["T1"], b["reason"])), key=lambda x: (x[1], x[0]))
    mints = [meta[int(m)] for m in mid]
    return {"rows_md5": lv.rows_md5(mints, T, sd, X, score), "trades_md5": lv.trades_md5(trades), "n_rows": len(mints), "trades": trades, "d3": rep["d3_dropped"]}


@pytest.fixture(scope="module")
def boosters():
    pytest.importorskip("lightgbm")
    from tools import r4b_score as rs

    return rs.load_boosters()


# ------------------------------------------------------------------------------------------------------------------------------- equivalence
def test_replay_equals_pinned_batch_path(walk, tmp_path, boosters):
    bat = batch_path(walk, tmp_path / "batch", boosters)
    rep = lv.replay_walker(walk, H0, H1, W0, E, static_map=None, universe_min_mbt=BT0, boosters=boosters, pda_fn=pda_of, allow_dir=True,
                           log_root=tmp_path / "declog")
    assert bat["n_rows"] > 40 and len(bat["trades"]) > 10, "the scenario must exercise the book"
    assert {r for *_, r in bat["trades"]} >= {"rank", "end"}
    assert rep.n_rows == bat["n_rows"]
    assert rep.rows_md5 == bat["rows_md5"]
    assert rep.trades == bat["trades"]
    assert rep.trades_md5 == bat["trades_md5"]
    assert rep.counters["d3_dropped"] == bat["d3"] > 0
    assert "bond_rows_after_mslot_mints" not in rep.counters


def test_replay_writes_a_verifiable_declog_for_the_bridge(walk, tmp_path, boosters):
    from tools.arena_declog import DecisionLog

    sha = "ab12" * 10
    rep = lv.replay_walker(walk, H0, H1, W0, E, static_map=None, universe_min_mbt=BT0, boosters=boosters, pda_fn=pda_of, allow_dir=True,
                           log_root=tmp_path / "declog", frozen_sha=sha)
    log = DecisionLog(tmp_path / "declog")
    log.verify(lv.BOT_ID)  # raises on a chain break
    rows = log.rows(lv.BOT_ID)
    assert rows[0]["kind"] == "start" and rows[0]["frozen_sha"] == sha
    ent = [r for r in rows if r["kind"] == "entry"]
    ex = [r for r in rows if r["kind"] == "exit"]
    assert len(ent) == rep.counters["entries"] and len(ex) == len(rep.trades)
    assert all(r["pool"] == pda_of(r["meta"]["mint"]) and r["stake_sol"] == FZ.STAKE_SOL and r["order_id"] == f"{r['meta']['mint']}:{r['t_mark']}" for r in ent)
    assert {r["order_id"] for r in ex} == {r["order_id"] for r in ent}  # every entry is closed (end of replay)
    assert rep.log_md5 == log.md5()
    sd_by_t = {m.t: m.sd for m in rep.marks}
    assert all(isinstance(r["decision_slot"], int) and r["decision_slot"] == sd_by_t[r["t_mark"]] for r in ent)
    live_exits = [r for r in ex if r["reason"] in ("rank", "gap")]  # an 'end' exit is the replay's own closure, not a mark's decision
    assert live_exits and all(isinstance(r["decision_slot"], int) for r in live_exits)
    assert all(r["decision_slot"] == sd_by_t[r["t_mark"]] for r in live_exits if r["reason"] == "rank")


def test_start_row_default_frozen_sha_is_the_repo_head(walk, tmp_path, boosters):
    import subprocess

    from tools.arena_declog import DecisionLog

    head = subprocess.run(["git", "-C", str(Path(lv.__file__).resolve().parents[1]), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert lv.repo_head_sha() == head and len(head) == 40
    lv.replay_walker(walk, H0, "2026-08-14T04", W0, BT0 + 4 * 3600, static_map=None, universe_min_mbt=BT0, boosters=boosters, pda_fn=pda_of, allow_dir=True,
                     log_root=tmp_path / "declog")
    assert DecisionLog(tmp_path / "declog").rows(lv.BOT_ID)[0]["frozen_sha"] == head


def test_static_map_mode_equals_batch(tmp_path, boosters):
    root = tmp_path / "walk"
    make_walk(root, scenario(seed=11, n_members=13))
    smap = {pda_of(f"M{i:02d}"): V for i in range(13)} | {pda_of("XVHI"): 17_800_000_000}
    from tools import r4b_read as rr
    from tools import r4b_score as rs

    rl.build(root, H0, H1, tmp_path / "b", v_source="static_map", static_map=smap, canonical_fn=pda_of, problems_fn=lambda *_: [], verify=verify_table(root),
             memory_limit="1GB", threads=2)
    plan = SimpleNamespace(w0=datetime.fromtimestamp(W0, tz=timezone.utc), e=datetime.fromtimestamp(E, tz=timezone.utc))
    R, rep, con = rr.build_rows(tmp_path / "b", plan, {"delta_slots": 0.0}, {"2026-08-14": 0.4}, SimpleNamespace(threads=2, memory_limit="1GB"), BT0)
    meta = dict(con.sql(f"select mid, mint from read_parquet('{tmp_path / 'b'}/meta.parquet')").fetchall())
    con.close()
    mid, T, sd = (np.concatenate(getattr(R, k)) for k in ("mid", "T", "sd"))
    X = np.vstack(R.X)
    score = rs.bag_score(boosters, X)
    want = lv.rows_md5([meta[int(m)] for m in mid], T, sd, X, score)
    got = lv.replay_walker(root, H0, H1, W0, E, static_map=smap, universe_min_mbt=BT0, boosters=boosters, pda_fn=pda_of, allow_dir=True)
    assert got.rows_md5 == want


# ------------------------------------------------------------------------------------------------------------------------------- the book
def _xs_trades(T, mints, score):
    order = {m: i for i, m in enumerate(sorted(set(mints)))}
    mid = np.array([order[m] for m in mints], np.int64)
    b = bk.xs_book(np.asarray(T, np.int64), mid, np.asarray(score, float))
    inv = {i: m for m, i in order.items()}
    return sorted(((inv[int(m)], int(a), int(c), str(r)) for m, a, c, r in zip(b["mid"], b["T0"], b["T1"], b["reason"])), key=lambda x: (x[1], x[0]))


@pytest.mark.parametrize("seed", range(25))
def test_livebook_equals_xs_book(seed):
    rnd = np.random.default_rng(seed)
    universe = [f"m{i:03d}" for i in range(int(rnd.integers(5, 40)))]
    marks = np.arange(0, int(rnd.integers(5, 60)) * 3600, 3600) + 1_786_000_000 - (1_786_000_000 % 3600)
    keep_marks = marks[rnd.random(len(marks)) > 0.12]  # missing marks: gaps
    T, M, SC = [], [], []
    for t in keep_marks:
        live = [m for m in universe if rnd.random() < 0.6]
        for m in live:
            T.append(int(t))
            M.append(m)
            SC.append(float(rnd.integers(0, 6)) if seed % 3 == 0 else float(rnd.normal()))  # integer scores: many ties (lower mint wins)
    want = _xs_trades(T, M, SC)
    book = lv.LiveBook()
    acts = []
    for t in keep_marks:
        idx = [i for i in range(len(T)) if T[i] == t]
        ranked = [M[i] for i in sorted(idx, key=lambda i: (-SC[i], M[i]))]
        acts += book.step(int(t), ranked)
    acts += book.close_all()
    assert lv.trades_from_actions(acts) == want


def test_livebook_empty_mark_closes_as_gap_at_the_same_t1():
    t0 = 1_786_003_200
    book = lv.LiveBook(k=2)
    a = book.step(t0, ["a", "b", "c"])
    assert [x[1] for x in a if x[0] == "entry"] == ["a", "b"]
    g = book.step(t0 + 3600, [])
    assert sorted(g) == [("exit", "a", t0, t0 + 3600, "gap"), ("exit", "b", t0, t0 + 3600, "gap")]
    assert book.step(t0 + 7200, ["c"]) == [("entry", "c", t0 + 7200, 0)]
    T = [t0] * 3 + [t0 + 7200]
    assert _xs_trades(T, ["a", "b", "c", "c"], [3.0, 2.0, 1.0, 1.0])[:2] == sorted(lv.trades_from_actions(a + g))[:2]


# ------------------------------------------------------------------------------------------------------------------------------- ingest
def test_row_ingest_equals_block_ingest(walk):
    con = duckdb.connect()
    hf = lambda s: int(con.execute("select hash(?::varchar)", [s]).fetchone()[0])  # noqa: E731
    a = lv.Engine(universe_min_mbt=BT0, pda_fn=pda_of, hash_fn=hf, score_fn=lambda b, X: X[:, 0])
    b = lv.Engine(universe_min_mbt=BT0, pda_fn=pda_of, hash_fn=hf, score_fn=lambda b, X: X[:, 0])
    for hix, h in enumerate(rl.hour_range(H0, "2026-08-14T08")):
        if h < H1:
            for i, r in enumerate(lv._read_rows(con, walk / "migrations" / f"migrations-{h}.jsonl.zst", rl.MIG_COLS)):
                a.ingest_migration(r, frn=hix * lv.FRN_HOUR + i)
                b.ingest_migration(r, frn=hix * lv.FRN_HOUR + i)
            for r in lv._read_rows(con, walk / "creates" / f"creates-{h}.jsonl.zst", rl.CREATE_COLS):
                a.ingest_create(r)
                b.ingest_create(r)
        rows = lv._read_rows(con, walk / "trades" / f"trades-{h}.jsonl.zst", rl.TRADE_COLS)
        for i, r in enumerate(rows):
            a.ingest_trade(r, frn=hix * lv.FRN_HOUR + i)
        b.ingest_trade_block(lv._read_trades(con, walk / "trades" / f"trades-{h}.jsonl.zst", hix))
    assert a.clock == b.clock and a.mkt == b.mkt and a.ncr == b.ncr
    assert set(a.mints) == set(b.mints)
    for m in a.mints:
        sa, sb = a.mints[m], b.mints[m]
        assert {k: v.tolist() for k, v in sa.prints.items()} == {k: v.tolist() for k, v in sb.prints.items()}, m
        assert (sa.b_n, sa.b_buy, sa.b_sell, sa.b_by_th, sa.ch, sa.mslot, sa.mbt, sa.mev_pool) == (sb.b_n, sb.b_buy, sb.b_sell, sb.b_by_th, sb.ch, sb.mslot, sb.mbt, sb.mev_pool)
    ma = [(m.mint, m.s0, m.v0) for m in a.members()]
    assert ma == [(m.mint, m.s0, m.v0) for m in b.members()] and len(ma) == 17  # 16 + XLATE; out: mayhem, non-WSOL, V high, no migration


def test_membership_equals_layout_meta(walk, tmp_path):
    rl.build(walk, H0, H1, tmp_path / "b", v_source="event", canonical_fn=pda_of, problems_fn=lambda *_: [], verify=verify_table(walk), memory_limit="1GB", threads=2)
    meta = duckdb.sql(f"select mint, pool, mslot, mbt, s0, v from read_parquet('{tmp_path / 'b'}/meta.parquet') order by mid").fetchall()
    con = duckdb.connect()
    eng = lv.Engine(universe_min_mbt=0, pda_fn=pda_of, hash_fn=lambda s: int(con.execute("select hash(?::varchar)", [s]).fetchone()[0]),
                    score_fn=lambda b, X: X[:, 0])
    for hix, h in enumerate(rl.hour_range(H0, "2026-08-14T08")):
        if h < H1:
            for i, r in enumerate(lv._read_rows(con, walk / "migrations" / f"migrations-{h}.jsonl.zst", rl.MIG_COLS)):
                eng.ingest_migration(r, frn=hix * lv.FRN_HOUR + i)
            for r in lv._read_rows(con, walk / "creates" / f"creates-{h}.jsonl.zst", rl.CREATE_COLS):
                eng.ingest_create(r)
        eng.ingest_trade_block(lv._read_trades(con, walk / "trades" / f"trades-{h}.jsonl.zst", hix))
    got = [(m.mint, m.pool, m.mslot, m.mbt, m.s0, m.v0) for m in eng.members()]
    assert got == [tuple(r) for r in meta]


# ------------------------------------------------------------------------------------------------------------------------------- guards
def test_no_live_mode_and_no_send_path():
    assert lv.LIVE_BUILT is False
    with pytest.raises(lv.LiveRefused):
        lv.Engine(universe_min_mbt=0, mode="live")
    with pytest.raises(lv.LiveRefused):
        lv.Engine(universe_min_mbt=0, v_source="event", static_map={})
    src = inspect.getsource(lv)
    for word in ("probe_live", "LiveExecutor", "Keypair", "sendTransaction", "send_transaction", "private_key", "helius.env"):
        assert word not in src.split('"""', 2)[2], word  # nothing after the module docstring names a send path or a key


@pytest.mark.parametrize("p", ["/data/mal/blocks/forward-1016", "/data/mal/clean-view/fresh-0802", "/data/mal/arena/tape", "/data/mal/meta-capture",
                               "/data/mal/clean-view/fresh-0903", "/tmp/x", "/data/mal/clean-view/explore-0814/../fresh-0828/w1"])
def test_replay_refuses_non_exploration_paths(p):
    assert lv.path_problem(p) is not None
    with pytest.raises(lv.LiveRefused):
        lv.replay_walker(Path(p), "2026-08-14T00", "2026-08-14T06", W0, E, static_map={}, universe_min_mbt=0)


def test_exploration_paths_pass_the_guard():
    assert lv.path_problem("/data/mal/clean-view/explore-0814/w7") is None
    assert lv.path_problem("/data/mal/clean-view/oracle-live-2026-09-25_27") is None


def test_replay_refuses_october_hours(tmp_path):
    with pytest.raises(lv.LiveRefused, match="October"):
        lv.replay_walker(tmp_path, "2026-09-30T20", "2026-10-01T02", int(datetime(2026, 9, 30, 22, tzinfo=timezone.utc).timestamp()),
                         int(datetime(2026, 10, 1, 1, tzinfo=timezone.utc).timestamp()), static_map={}, universe_min_mbt=0, allow_dir=True)


def test_constants_follow_the_pinned_modules():
    from tools import r4b_count as rc
    from tools import r4b_read as rr

    assert lv.D3_DROP_S == rr.D3_DROP_S
    assert lv.MARK_S == rr.MARK_S == rc.MARK_S
    assert (lv.GRID_LO_S, lv.GRID_HI_S) == (rc.LIVE_LO_S, rc.LIVE_HI_S)
    assert lv.HORIZON_S == rl.HORIZON_S and lv.WINDOW_SLOTS == rl.WINDOW_SLOTS
    assert FZ.K == 10 and FZ.STAKE_SOL == 0.25


# ------------------------------------------------------------------------------------------------------------------------------- the single-mark feature path
def _rand_mint(rnd, mbt, n, ch_trader):
    t = np.sort(mbt + 30 + rnd.integers(0, 50 * 3600, n))
    t[rnd.random(n) < 0.03] = -1  # a few null block_times (kept in order by slot, as ut does)
    slot = S + np.arange(n) * 3 + rnd.integers(0, 2, n)
    isbuy = rnd.random(n) < 0.55
    sol = rnd.integers(10**7, 4 * 10**9, n).astype(float)
    q = rnd.integers(30 * 10**9, 200 * 10**9, n).astype(float)
    b = rnd.integers(10**14, 3 * 10**14, n).astype(float)
    tok = (b * 0.004 * rnd.random(n)).astype(float)
    th = rnd.choice(np.array([ch_trader] + [int(x) for x in rnd.integers(1, 2**63, 30)], np.uint64), n)
    vq = np.full(n, float(V)) + rnd.integers(0, 3, n)
    bt = np.where(t < 0, np.nan, t).astype(float)
    return slot, bt, isbuy, sol, tok, q, b, th, vq


@pytest.mark.parametrize("seed", range(6))
def test_features_at_equals_mint_features_row_on_every_grid_mark(seed):
    from tools import r4b_features as rf

    rnd = np.random.default_rng(seed)
    mbt = BT0 + 600
    secs = np.arange(BT0, BT0 + 60 * 3600, 1, dtype=np.int64)
    clock_slot = S + (secs - BT0) * 5 // 2
    mnt = np.arange(BT0, BT0 + 60 * 3600, 60, dtype=np.float64)
    cols = {k: rnd.integers(0, 50, len(mnt)).astype(float) * (10**9 if k.startswith("v") else 1) for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}
    gr = np.sort(BT0 + rnd.integers(0, 50 * 3600, 200))
    prep = rf.build_prep(secs, clock_slot, mnt, cols, gr, (S + (gr - BT0) * 5 // 2 + rnd.integers(0, 4000, 200)).astype(float))
    ch = int(rnd.integers(1, 2**63))
    n = int(rnd.integers(40, 900))
    sl, bt, ib, so, tk, q, b, th, vq = _rand_mint(rnd, mbt, n, ch)
    sl = S + np.sort(rnd.integers(0, 50 * 3600 * 5 // 2, n))
    btv = np.where(np.isnan(bt), np.nan, BT0 + (sl - S) * 2 // 5)
    const = rf.MintConst(mid=0, mbt=mbt, mslot=S, s0=int(sl[0]), v=float(V), cbt=float(mbt - 900) if seed % 2 else float("nan"), has_creator=True,
                         nb=12.0, ntr=7.0, bbuy=3e9, bsell=1e9, cre_sell=float("nan") if seed == 3 else 2e8)
    full = rf.mint_features(prep, const, ch if seed != 4 else None, sl, btv, ib, so, tk, q, b, th, vq=vq, grads_rule="asof_pda_first_print", mint_filters="asof")
    rows = {} if full is None else {int(t): full.X[i] for i, t in enumerate(full.I[:, 1])}
    t_lo = ((mbt + 600 + 119) // 120) * 120
    checked = 0
    for T in range(t_lo, mbt + 48 * 3600 + 1, 120):
        sd = int(prep.slot_at(np.array([T]))[0])
        k = sl < sd
        got = lv.features_at(prep, const, ch if seed != 4 else None, sl[k], btv[k], ib[k], so[k], tk[k], q[k], b[k], th[k], vq[k], T, sd) if k.any() else None
        want = rows.get(T)
        assert (got is None) == (want is None), T
        if got is not None:
            assert got.tobytes() == want.tobytes(), (T, [rf.FEATS[i] for i in np.flatnonzero(got.view(np.uint32) != want.view(np.uint32))])
            checked += 1
    assert checked > 30


def test_exact_grid_engine_equals_fast_engine(walk, tmp_path, boosters):
    fast = lv.replay_walker(walk, H0, H1, W0, E, static_map=None, universe_min_mbt=BT0, boosters=boosters, pda_fn=pda_of, allow_dir=True)
    orig = lv.Engine.__init__

    def slow_init(self, *a, **k):
        orig(self, *a, **{**k, "exact_grid": True})

    lv.Engine.__init__ = slow_init
    try:
        slow = lv.replay_walker(walk, H0, H1, W0, E, static_map=None, universe_min_mbt=BT0, boosters=boosters, pda_fn=pda_of, allow_dir=True)
    finally:
        lv.Engine.__init__ = orig
    assert (fast.rows_md5, fast.trades_md5) == (slow.rows_md5, slow.trades_md5)


def test_sorted4_matches_lexsort():
    rnd = np.random.default_rng(3)
    for _ in range(300):
        n = int(rnd.integers(0, 40))
        cols = [rnd.integers(0, 3, n) for _ in range(4)]
        if rnd.random() < 0.5:  # sorted case
            o = np.lexsort(cols[::-1])
            cols = [c[o] for c in cols]
        o = np.lexsort(cols[::-1])
        want = bool(all((np.array([c[o] for c in cols]) == np.array(cols)).all(axis=0))) if n else True
        assert lv._sorted4(*cols) == want


# ------------------------------------------------------------------------------------------------------------------------------- live-order feeding (engine API, arrival order)
def _score_ret(b, X):
    from tools import r4b_features as rf

    return np.nan_to_num(X[:, rf.FEATS.index("ret_900")].astype(np.float64), nan=-9.0)


def mk_engine(**kw):
    kw.setdefault("universe_min_mbt", BT0)
    kw.setdefault("pda_fn", pda_of)
    kw.setdefault("hash_fn", lv.DuckHash())
    if "boosters" not in kw:
        kw.setdefault("score_fn", _score_ret)
    return lv.Engine(**kw)


def event_stream(hours: dict, complete_delay: int = 0) -> list:
    """Every walker row of the scenario as (arrival second, kind order, original position, kind, row) sorted by arrival. complete_delay delays the whole
    migrations stream (complete + migration rows) behind the trades, as a live feed that delivers streams out of chain order."""
    ev, n = [], 0
    for d in hours.values():
        for sub, kind, o in (("creates", "create", 0), ("migrations", "migration", 1), ("trades", "trade", 2)):
            for r in d[sub]:
                ev.append((r["block_time"] + (complete_delay if kind == "migration" else 0), o, n, kind, r))
                n += 1
    ev.sort(key=lambda e: e[:3])
    return ev


def feed(eng, ev: list, i: int, until: int) -> int:
    while i < len(ev) and ev[i][0] < until:
        kind, r = ev[i][3], ev[i][4]
        {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[kind](r)
        i += 1
    return i


def drive(ev: list, marks, lag: int = 30, **kw):
    """Feed the stream up to T + lag, decide T, for each mark: the live call pattern. Returns (engine, [MarkResult])."""
    eng, i, out = mk_engine(**kw), 0, []
    for T in marks:
        i = feed(eng, ev, i, T + lag)
        out.append(eng.decide(T, w0=W0, last_entry=E - 3600))
    return eng, out


MARKS = list(range(W0, E, 3600))


def sig(res) -> tuple:
    return (res.t, res.sd, res.mints, res.sdv.tobytes(), res.X.tobytes(), res.score.tobytes(), res.ranked, res.actions, res.d3_dropped)


def test_live_order_feed_equals_pinned_batch_path(walk, tmp_path, boosters):
    """Engine fed in arrival order, each mark decided on the data up to T + 30 s only (pruning on, the default), equals the pinned batch decisions."""
    bat = batch_path(walk, tmp_path / "batch", boosters)
    eng, res = drive(event_stream(scenario()), MARKS, boosters=boosters)
    acts = [a for r in res for a in r.actions] + eng.book.close_all("end")
    allm = [m for r in res for m in r.mints]
    got = lv.rows_md5(allm, np.concatenate([r.T for r in res]), np.concatenate([r.sdv for r in res]), np.vstack([r.X for r in res]),
                      np.concatenate([r.score for r in res]))
    assert len(allm) == bat["n_rows"] > 40
    assert got == bat["rows_md5"]
    assert lv.trades_from_actions(acts) == bat["trades"]
    assert eng.counters.get("clock_pruned", 0) > 0 and eng.counters.get("mkt_pruned", 0) > 0  # the default pruning was active


# ------------------------------------------------------------------------------------------------------------------------------- clock guard, mark order
def _tiny_engine():
    return mk_engine(universe_min_mbt=0)


def _tape_row(bt: int, slot: int, mint: str = "FILL"):
    return {"venue": "pump_bonding", "mint": mint, "trader": "t", "side": "buy", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 1, "base_reserve": 1,
            "pool": None, "slot": slot, "signature": f"s{bt}", "event_index": 0, "tx_index": None, "block_time": bt}


def test_decide_refuses_until_the_second_at_or_after_T_is_complete():
    T = BT0 + 3 * 3600
    eng = _tiny_engine()
    with pytest.raises(lv.LiveRefused, match="clock not complete"):  # an empty clock
        eng.decide(T)
    eng.ingest_trade(_tape_row(T - 5, 100))
    with pytest.raises(lv.LiveRefused, match="clock not complete"):  # an early call: the clock ends before T
        eng.decide(T)
    eng.ingest_trade(_tape_row(T, 110))
    with pytest.raises(lv.LiveRefused, match="clock not complete"):  # second T is the latest second: rows of T may still be arriving
        eng.decide(T)
    eng.ingest_trade(_tape_row(T + 3, 120))
    eng.ingest_trade(_tape_row(T + 4, 121))  # no second T+1, T+2 was seen, but a second after the first one >= T is
    assert eng._last_T is None and eng.book.last is None  # the refusals changed nothing
    res = eng.decide(T)
    assert res.t == T and res.sd == 110 and res.mints == []  # sd = min slot of second T


def test_decide_refuses_a_clock_whose_only_later_second_is_the_first_one_after_T():
    T = BT0 + 3 * 3600
    eng = _tiny_engine()
    eng.ingest_trade(_tape_row(T - 10, 100))
    eng.ingest_trade(_tape_row(T + 3, 120))  # clock = {T-10, T+3}: the latest second is >= T + 1, but it is the first second at or after T
    assert eng._clock_hi >= T + 1
    with pytest.raises(lv.LiveRefused, match="clock not complete"):
        eng.decide(T)
    eng.ingest_trade(_tape_row(T + 3, 119))  # a late row of the same second lowers its min slot but does not complete it
    with pytest.raises(lv.LiveRefused, match="clock not complete"):
        eng.decide(T)
    eng.ingest_trade(_tape_row(T + 6, 130))
    assert eng.decide(T).sd == 119


def test_decide_refuses_a_repeated_or_earlier_mark_and_livebook_step_too():
    T = BT0 + 3 * 3600
    eng = _tiny_engine()
    for k in range(-2, 12000, 1000):
        eng.ingest_trade(_tape_row(T + k, 100 + k // 3))
    eng.decide(T + 3600)
    for t in (T + 3600, T, T - 3600):
        with pytest.raises(lv.LiveRefused, match="not after the last decided mark"):
            eng.decide(t)
    book = lv.LiveBook(k=2)
    book.step(T, ["a", "b"])
    book.step(T + 3600, [])  # an empty mark counts as stepped
    for t in (T + 3600, T + 1800, T):
        with pytest.raises(lv.LiveRefused, match="not after the last stepped mark"):
            book.step(t, ["a"])
    assert book.step(T + 7200, ["c"])[-1][:2] == ("entry", "c")


def test_livebook_trailing_empty_mark_is_gap_live_but_end_in_xs_book():
    """Documented live-vs-read difference (LiveBook docstring): an outage that runs to the end of the window. Same T0 and T1, different label."""
    t0 = 1_786_003_200
    book = lv.LiveBook()
    acts = book.step(t0, ["a", "b", "c"])
    assert [a[0] for a in acts] == ["entry"] * 3
    gap = book.step(t0 + 3600, [])  # first empty mark: closes now, 'gap', at last non-empty mark + 1 h
    assert sorted(gap) == [("exit", m, t0, t0 + 3600, "gap") for m in "abc"]
    assert book.step(t0 + 7200, []) == [] and book.close_all() == []  # nothing left to close
    live = lv.trades_from_actions(acts + gap)
    xs = _xs_trades([t0] * 3, ["a", "b", "c"], [3.0, 2.0, 1.0])  # xs_book never sees the empty marks
    assert [(m, a, b) for m, a, b, _ in live] == [(m, a, b) for m, a, b, _ in xs]
    assert {r for *_, r in live} == {"gap"} and {r for *_, r in xs} == {"end"}


# ------------------------------------------------------------------------------------------------------------------------------- bounded growth
def test_pruned_engine_decides_like_an_unpruned_one(boosters):
    ev = event_stream(scenario())
    full_eng, full = drive(ev, MARKS, keep_clock_s=None)
    pr_eng, pruned = drive(ev, MARKS)
    assert [sig(r) for r in pruned] == [sig(r) for r in full]
    assert sum(len(r.mints) for r in full) > 40 and any(r.actions for r in full)
    assert pr_eng.counters["clock_pruned"] > 0 and pr_eng.counters["mkt_pruned"] > 0
    assert "clock_pruned" not in full_eng.counters
    assert len(pr_eng.clock) < len(full_eng.clock) and len(pr_eng.mkt) < len(full_eng.mkt) and len(pr_eng.ncr) <= len(full_eng.ncr)
    assert min(pr_eng.clock) >= MARKS[-1] - lv.CLOCK_KEEP_S and min(pr_eng.mkt) >= MARKS[-1] - lv.CLOCK_KEEP_S
    with pytest.raises(lv.LiveRefused):
        mk_engine(exact_grid=True).prune_clock(W0)  # exact_grid reads old grid seconds


def test_exact_grid_never_prunes_the_clock():
    ev = event_stream(scenario())
    eng, _ = drive(ev, MARKS[:2], exact_grid=True)
    assert eng.keep_clock_s is None and "clock_pruned" not in eng.counters and min(eng.clock) < MARKS[1] - lv.CLOCK_KEEP_S


def test_duckhash_cache_is_a_bounded_lru_and_values_are_stable():
    con = duckdb.connect()
    want = lambda s: int(con.execute("select hash(?::varchar)", [s]).fetchone()[0])  # noqa: E731
    h = lv.DuckHash(cache_max=50)
    first = {f"TR{i}": h(f"TR{i}") for i in range(200)}
    assert len(h._cache) == 50 and "TR199" in h._cache and "TR0" not in h._cache
    assert all(h(k) == v == want(k) for k, v in first.items())  # evicted keys are recomputed to the same value
    assert h(None) == want(None) and len(h._cache) <= 50
    h("TR150")  # a hit refreshes recency: it survives 49 more inserts
    for i in range(300, 349):
        h(f"TR{i}")
    assert "TR150" in h._cache and lv.HASH_CACHE_MAX == 200_000 and lv.DuckHash().cache_max == 200_000


def test_ingest_hashes_the_trader_of_kept_rows_only():
    calls = []

    def hf(s):
        calls.append(s)
        return len(calls)

    eng = lv.Engine(universe_min_mbt=0, pda_fn=pda_of, hash_fn=hf, score_fn=_score_ret, bond_mints={"MB"})

    def ps(mint, pool, trader, slot=200):
        return {"venue": "pumpswap", "mint": mint, "pool": pool, "trader": trader, "side": "buy", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 1,
                "base_reserve": 1, "slot": slot, "block_time": BT0 + 5, "virtual_quote_reserves": V}

    eng.ingest_trade({**_tape_row(BT0, 100, "OTHER"), "trader": "untracked_bond"})  # a bonding mint outside bond_mints
    eng.ingest_trade(ps("MQ", pda_of("MQ"), "pending_trader"))  # no complete row yet: buffered, not hashed
    eng.ingest_migration({"type": "complete", "mint": "MP", "slot": 150, "block_time": BT0})
    eng.ingest_trade(ps("MP", "SOME_OTHER_POOL", "wrong_pool_trader"))  # a completed mint's print on a non-PDA pool
    assert calls == []
    eng.ingest_trade(ps("MP", pda_of("MP"), "kept_pumpswap_trader"))
    eng.ingest_trade({**_tape_row(BT0, 140, "MB"), "trader": "kept_bonding_trader"})
    eng.ingest_trade({**_tape_row(BT0, 151, "MB"), "trader": "after_mslot_trader"})  # skipped: slot > mslot is not known yet for MB (no complete), so kept
    eng.ingest_migration({"type": "complete", "mint": "MB", "slot": 145, "block_time": BT0})
    eng.ingest_trade({**_tape_row(BT0, 146, "MB"), "trader": "skipped_after_mslot_trader"})
    assert calls == ["kept_pumpswap_trader", "kept_bonding_trader", "after_mslot_trader"]
    eng.ingest_migration({"type": "complete", "mint": "MQ", "slot": 190, "block_time": BT0})  # the buffered print is replayed and hashed now
    assert calls[-1] == "pending_trader" and len(calls) == 4
