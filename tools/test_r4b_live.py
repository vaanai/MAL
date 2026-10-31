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


def live_order_vs_batch(hours: dict, tmp_path: Path, boosters) -> tuple[dict, lv.Engine, list, str, list]:
    """Write `hours` as a walker dir, run the pinned batch path on it, and drive the engine in arrival order (each mark on the data up to T + 30 s only,
    default pruning). Returns (batch result, engine, mark results, rows md5, trades)."""
    root = tmp_path / "walk"
    make_walk(root, hours)
    bat = batch_path(root, tmp_path / "batch", boosters)
    eng, res = drive(event_stream(hours), MARKS, boosters=boosters)
    acts = [a for r in res for a in r.actions] + eng.book.close_all("end")
    allm = [m for r in res for m in r.mints]
    got = lv.rows_md5(allm, np.concatenate([r.T for r in res]), np.concatenate([r.sdv for r in res]), np.vstack([r.X for r in res]),
                      np.concatenate([r.score for r in res]))
    return bat, eng, res, got, lv.trades_from_actions(acts)


def test_live_order_feed_equals_pinned_batch_path(tmp_path, boosters):
    """Engine fed in arrival order, each mark decided on the data up to T + 30 s only (pruning on, the default), equals the pinned batch decisions."""
    bat, eng, res, got, trades = live_order_vs_batch(scenario(), tmp_path, boosters)
    assert sum(len(r.mints) for r in res) == bat["n_rows"] > 40
    assert got == bat["rows_md5"] and trades == bat["trades"]
    assert eng.counters.get("clock_pruned", 0) > 0 and "mkt_pruned" not in eng.counters  # clock seconds pruned; market minutes never


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
    assert pr_eng.counters["clock_pruned"] > 0 and "mkt_pruned" not in pr_eng.counters
    assert "clock_pruned" not in full_eng.counters
    assert len(pr_eng.clock) < len(full_eng.clock) and min(pr_eng.clock) >= MARKS[-1] - lv.CLOCK_KEEP_S
    assert pr_eng.mkt == full_eng.mkt and pr_eng.ncr == full_eng.ncr  # market and create minutes are never pruned (float cumsum prefix = the read's)
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


# ------------------------------------------------------------------------------------------------------------------------------- bonding rows (reviewer note 3)
def _bond_engine(rows: list[dict], *, complete_first: bool = True, creator: str | None = "CRE", mslot: int = 1000):
    """An engine fed one bonding mint's rows plus its complete (and create) row. Returns (engine, Member)."""
    con = duckdb.connect()
    eng = lv.Engine(universe_min_mbt=0, pda_fn=pda_of, hash_fn=lambda s: int(con.execute("select hash(?::varchar)", [s]).fetchone()[0]), score_fn=_score_ret)
    if creator is not None:
        eng.ingest_create({"type": "create", "mint": "MB", "creator": creator, "slot": 900, "block_time": BT0 - 100})
    comp = {"type": "complete", "mint": "MB", "slot": mslot, "block_time": BT0}
    if complete_first:
        eng.ingest_migration(comp)
    for r in rows:
        eng.ingest_trade({"venue": "pump_bonding", "mint": "MB", "block_time": BT0 - 5, "pool": None, **r})
    if not complete_first:
        eng.ingest_migration(comp)
    st = eng.mints["MB"]
    return eng, lv.Member("MB", pda_of("MB"), mslot, BT0, mslot + 1, V, st)


def test_bonding_rows_after_mslot_are_skipped_and_counted_once_mslot_is_known():
    rows = [dict(trader="a", side="buy", sol_lamports=5, slot=990), dict(trader="b", side="sell", sol_lamports=3, slot=1000),  # <= mslot: kept
            dict(trader="c", side="buy", sol_lamports=7, slot=1001), dict(trader="a", side="sell", sol_lamports=9, slot=1500)]  # > mslot: skipped
    eng, m = _bond_engine(rows)
    st = eng.mints["MB"]
    assert (st.b_n, st.b_buy, st.b_sell, len(st.b_by_th)) == (2, 5, 3, 2)
    assert eng.counters["bond_rows_after_mslot_skipped"] == 2 and "bond_rows_after_mslot_mints" not in eng.counters
    c, _ = eng.mint_const(m, 0)
    assert (c.nb, c.ntr, c.bbuy, c.bsell) == (2.0, 2.0, 5.0, 3.0) and "bond_rows_after_mslot_mints" not in eng.counters


def test_block_ingest_skips_bonding_rows_after_mslot_like_the_row_path():
    con = duckdb.connect()
    hf = lambda s: int(con.execute("select hash(?::varchar)", [s]).fetchone()[0])  # noqa: E731
    eng = lv.Engine(universe_min_mbt=0, pda_fn=pda_of, hash_fn=hf, score_fn=_score_ret)
    eng.ingest_migration({"type": "complete", "mint": "MB", "slot": 1000, "block_time": BT0})
    sl = np.array([999, 1000, 1001, 1002], np.int64)
    n = len(sl)
    cols = {"venue": np.array(["pump_bonding"] * n, dtype=object), "mint": np.array(["MB"] * n, dtype=object), "pool": np.array([None] * n, dtype=object),
            "slot": sl, "txi": np.full(n, -1, np.int64), "evi": np.zeros(n, np.int64), "frn": np.arange(n, dtype=np.int64), "bt": np.full(n, float(BT0)),
            "sol": np.full(n, 5.0), "tok": np.full(n, 1.0), "q": np.full(n, 1.0), "b": np.full(n, 1.0), "vq": np.full(n, np.nan),
            "isbuy": np.array([True, False, True, True], dtype=object), "th": np.array([hf("a"), hf("b"), hf("c"), hf("d")], np.uint64)}
    eng.ingest_trade_block(cols)
    st = eng.mints["MB"]
    assert (st.b_n, st.b_buy, st.b_sell, len(st.b_by_th)) == (2, 5, 5, 2) and eng.counters["bond_rows_after_mslot_skipped"] == 2


def test_bonding_rows_before_the_complete_row_are_kept_and_raise_the_alarm_once():
    rows = [dict(trader="a", side="buy", sol_lamports=5, slot=990), dict(trader="c", side="buy", sol_lamports=7, slot=1001)]
    eng, m = _bond_engine(rows, complete_first=False)  # mslot unknown when the second row arrives: it cannot be skipped
    assert eng.mints["MB"].b_n == 2 and "bond_rows_after_mslot_skipped" not in eng.counters
    for _ in range(3):  # three marks read the constants: still one alarm for the mint
        eng.mint_const(m, 0)
    assert eng.counters["bond_rows_after_mslot_mints"] == 1


@pytest.mark.parametrize("seed", range(60))
def test_bonding_constants_equal_the_layout_sql_including_null_sums(seed):
    """The prep_work SQL of r4b_layout.build (bond rows slot < mslot + 1 joined to the creator hash) evaluated by DuckDB on random rows, with NULL sides and
    NULL sols, against Engine.mint_const: nb, ntr, bbuy, bsell, cre_sell equal, NaN where SUM is NULL (and the alarm-free skip of rows after mslot)."""
    rnd = random.Random(seed)
    creator = None if seed % 6 == 0 else "CRE"
    sides = ["buy", "sell"] if seed % 2 else ["buy", "sell", None]
    p_null = 0.75 if seed % 3 else 0.3
    rows = [dict(trader=rnd.choice(["CRE", "T1", "T2"]), side=rnd.choice(sides), sol_lamports=None if rnd.random() < p_null else rnd.randint(1, 10**9),
                 slot=rnd.choice([990, 1000, 1000, 1001])) for _ in range(rnd.randint(1, 5))]
    eng, m = _bond_engine(rows, creator=creator)
    const, _ = eng.mint_const(m, 0)
    got = [const.nb, const.ntr, const.bbuy, const.bsell, const.cre_sell]
    con = duckdb.connect()
    con.sql("create table b(slot bigint, isbuy boolean, sol bigint, trader varchar)")
    for r in rows:
        con.execute("insert into b values (?, ?, ?, ?)", [r["slot"], None if r["side"] is None else r["side"] == "buy", r["sol_lamports"], r["trader"]])
    ch = "null::ubigint" if creator is None else "hash('CRE')"
    sql = con.sql(f"""select count(*) nb, count(distinct th) ntr, sum(case when isbuy then sol else 0 end) bbuy, sum(case when not isbuy then sol else 0 end) bsell,
                      sum(case when (not isbuy) and th = ch then sol else 0 end) cre_sell
                      from (select b.*, hash(trader) th from b where slot < 1000 + 1) x, (select {ch} ch) c""").fetchone()
    want = [float("nan")] * 5 if sql[0] == 0 else [float(v) if v is not None else float("nan") for v in sql]
    assert np.array_equal(np.array(got), np.array(want), equal_nan=True), (rows, got, want)


def test_bonding_sum_null_corners_reach_the_features_as_nan():
    """All rows buys with a NULL sol: bbuy NaN, bsell 0 (the ELSE 0 terms), cre_sell 0; all creator sells with NULL sol: cre_sell NaN too."""
    eng, m = _bond_engine([dict(trader="a", side="buy", sol_lamports=None, slot=990), dict(trader="b", side="buy", sol_lamports=None, slot=991)])
    c, _ = eng.mint_const(m, 0)
    assert np.isnan(c.bbuy) and c.bsell == 0.0 and c.cre_sell == 0.0 and c.nb == 2.0
    eng, m = _bond_engine([dict(trader="CRE", side="sell", sol_lamports=None, slot=990)])
    c, _ = eng.mint_const(m, 0)
    assert c.bbuy == 0.0 and np.isnan(c.bsell) and np.isnan(c.cre_sell)
    eng, m = _bond_engine([dict(trader="CRE", side="sell", sol_lamports=None, slot=990), dict(trader="x", side="buy", sol_lamports=4, slot=991)])
    c, _ = eng.mint_const(m, 0)
    assert c.cre_sell == 0.0 and c.bbuy == 4.0 and c.bsell == 0.0  # the buy's ELSE 0 term makes the sell sum non-NULL


# ------------------------------------------------------------------------------------------------------------------------------- QP N2: ties across rank 10, the activity window
def rename_mints(hours: dict, ren: dict) -> dict:
    out = {}
    for h, d in hours.items():
        out[h] = {sub: [{**r, **({"mint": ren[r["mint"]]} if r.get("mint") in ren else {}),
                         **({"pool": pda_of(ren[r["mint"]])} if r.get("mint") in ren and r.get("pool") == pda_of(r["mint"]) else {})} for r in rows]
                  for sub, rows in d.items()}
    return out


def test_score_tie_straddling_rank_10_goes_to_the_lower_mbt_then_mint(tmp_path, boosters):
    """Every row scores the same, so rank 10 / 11 is a tie among members that graduated at different times. The mints are renamed so that name order is the
    REVERSE of graduation order: (mbt, mint) is told apart from mint alone and from (-mbt, mint). The engine equals the pinned xs_book and ranks by (mbt, mint)."""
    ren = {f"M{i:02d}": f"R{15 - i:02d}" for i in range(16)}
    hours = rename_mints(scenario(), ren)
    root = tmp_path / "walk"
    make_walk(root, hours)
    mbt_of = {r["mint"]: r["block_time"] for d in hours.values() for r in d["migrations"] if r["type"] == "complete"}
    const = lambda b, X: np.ones(len(X))  # noqa: E731
    bat = batch_path(root, tmp_path / "batch", boosters, score_fn=const)
    rep = lv.replay_walker(root, H0, H1, W0, E, static_map=None, universe_min_mbt=BT0, boosters=boosters, score_fn=const, pda_fn=pda_of, allow_dir=True)
    assert any(len(r.ranked) > 10 for r in rep.marks), "the scenario must have a tie that straddles rank 10"
    for r in rep.marks:
        assert r.ranked == sorted(r.mints, key=lambda m: (mbt_of[m], m)), r.t
        assert {a[1] for a in r.actions if a[0] == "entry"} <= set(r.ranked[:10])
    big = next(r for r in rep.marks if len(r.ranked) > 10)
    assert mbt_of[big.ranked[9]] < mbt_of[big.ranked[10]] and big.ranked[9] > big.ranked[10]  # rank 10 / 11: earlier mbt wins although its name sorts later
    assert (rep.rows_md5, rep.trades_md5) == (bat["rows_md5"], bat["trades_md5"]) and rep.trades == bat["trades"]


def _without_prints(hours: dict, mint: str, lo: int, hi: int) -> dict:
    return {h: {**d, "trades": [r for r in d["trades"] if not (r["mint"] == mint and r["venue"] == "pumpswap" and lo <= r["block_time"] < hi)]}
            for h, d in hours.items()}


def test_member_whose_last_print_is_300_to_600_s_before_the_mark_has_a_row(tmp_path, boosters):
    """The activity pre-check is `latest print >= T - 600`; a member quiet for the last 5 minutes (and the 30 s the live feed has delivered past T) still
    gets a row, as in mint_features (btm in [T-600, T)). Fed in arrival order, so the engine has seen no print of this mint after T + 30."""
    T, mint = BT0 + 4 * 3600, "M03"
    hours = scenario()
    prev = [r["block_time"] for h in hours.values() for r in h["trades"] if r["mint"] == mint and r["venue"] == "pumpswap" and r["block_time"] < T - 300]
    assert T - 600 <= max(prev) < T - 300, "scenario precondition: M03 prints in [T-600, T-300)"
    bat, eng, res, got, trades = live_order_vs_batch(_without_prints(hours, mint, T - 300, T + 30), tmp_path, boosters)
    assert mint in next(r for r in res if r.t == T).mints
    assert (got, trades) == (bat["rows_md5"], bat["trades"]) and len(bat["trades"]) > 10


def test_member_whose_last_print_is_older_than_600_s_has_no_row(tmp_path, boosters):
    T, mint = BT0 + 4 * 3600, "M03"
    hours = _without_prints(scenario(), mint, T - 900, T + 30)
    last = max(r["block_time"] for h in hours.values() for r in h["trades"] if r["mint"] == mint and r["venue"] == "pumpswap" and r["block_time"] < T)
    assert last < T - 600
    bat, eng, res, got, trades = live_order_vs_batch(hours, tmp_path, boosters)
    assert mint not in next(r for r in res if r.t == T).mints
    assert (got, trades) == (bat["rows_md5"], bat["trades"])


# ------------------------------------------------------------------------------------------------------------------------------- QP N3: prints before their complete row
def test_out_of_order_streams_decide_like_the_in_order_stream():
    """The migrations stream (complete + migration rows) arrives 20 s behind the trades, so the first PumpSwap prints of every mint come before its
    complete row. The pending buffer replays them in arrival order: every mark's rows, scores, ranks and actions equal the in-order stream's."""
    hours = scenario()
    in_eng, in_order = drive(event_stream(hours), MARKS)
    late_eng, late = drive(event_stream(hours, complete_delay=20), MARKS)
    assert [sig(r) for r in late] == [sig(r) for r in in_order]
    assert sum(len(r.mints) for r in in_order) > 40 and any(r.actions for r in in_order)
    assert late_eng.counters["pending_buffered"] > 16 and late_eng.counters["pending_replayed"] == late_eng.counters["pending_buffered"]
    assert "pending_buffered" not in in_eng.counters and "pending_expired" not in late_eng.counters
    assert not late_eng._pending and late_eng._pending_n == 0  # everything buffered was replayed (or dropped): nothing left behind
    ms_in, ms_late = {m.mint: m for m in in_eng.members()}, {m.mint: m for m in late_eng.members()}
    assert ms_in.keys() == ms_late.keys() and all((ms_in[k].s0, ms_in[k].v0) == (ms_late[k].s0, ms_late[k].v0) for k in ms_in)


def _ps_row(mint: str, slot: int, bt: int | None, pool: str | None = "default", trader: str = "t", frn: int | None = None) -> dict:
    return {"venue": "pumpswap", "mint": mint, "pool": pda_of(mint) if pool == "default" else pool, "trader": trader, "side": "buy", "sol_lamports": 10**9,
            "token_raw": 10**6, "quote_reserve": 85 * 10**9, "base_reserve": 206 * 10**12, "slot": slot, "signature": f"s{slot}", "event_index": 0,
            "tx_index": None, "block_time": bt, "virtual_quote_reserves": V}


def _clock_row(bt: int, slot: int):
    return _tape_row(bt, slot, "CLOCKMINT")


def test_pending_prints_keep_arrival_order_and_expire_after_900_s():
    eng = _tiny_engine()
    t = BT0 + 5000
    eng.ingest_trade(_ps_row("MQ", 300, t, trader="first"))
    eng.ingest_trade(_ps_row("MQ", 300, t + 1, trader="second"))  # same slot: arrival order decides (frn)
    eng.ingest_trade(_ps_row("MQ", 299, t + 2, trader="third"))
    eng.ingest_trade(_ps_row("MQ", 301, None, trader="no_bt"))  # a null block_time is kept (aged by the clock at arrival)
    eng.ingest_trade(_ps_row("MQ", 302, t + 3, pool=None))  # no pool: never the PDA pool, not buffered
    eng.ingest_trade(_ps_row("MQ", 303, t + 4, pool="NOT_THE_PDA"))  # buffered (the pool is checked at replay), then dropped
    assert eng.counters["pending_buffered"] == 5 and eng._pending_n == 5
    eng.ingest_migration({"type": "complete", "mint": "MQ", "slot": 250, "block_time": t - 20})
    p = eng.mints["MQ"].prints
    assert [int(x) for x in p["slot"]] == [300, 300, 299, 301]  # arrival order; the non-PDA row did not get in
    assert [int(x) for x in p["frn"]] == sorted(int(x) for x in p["frn"])
    assert eng.counters["pending_replayed"] == 5 and eng._pending_n == 0 and not eng._pending
    # a print older than 900 s of block_time (against the latest second ingested) is dropped on arrival, and a complete row that comes too late finds none
    eng.ingest_trade(_clock_row(t + 2000, 400))
    eng.ingest_trade(_ps_row("MR", 310, t + 1, trader="old"))
    assert eng.counters["pending_expired"] == 1 and "MR" not in eng._pending
    eng.ingest_trade(_ps_row("MR", 410, t + 1990, trader="young"))
    eng.ingest_trade(_clock_row(t + 1990 + 1000, 500))  # the clock moves 1000 s on: the buffered print is now older than 900 s
    eng.ingest_migration({"type": "complete", "mint": "MR", "slot": 405, "block_time": t + 1980})
    assert len(eng.mints["MR"].prints["slot"]) == 0 and eng.counters["pending_expired"] == 2
    eng.ingest_trade(_ps_row("MR", 420, t + 2995))  # a print after the complete row is added directly
    assert len(eng.mints["MR"].prints["slot"]) == 1


def test_pending_sweep_runs_with_the_clock_and_the_buffer_is_bounded(monkeypatch):
    eng = _tiny_engine()
    t = BT0 + 5000
    eng.ingest_trade(_ps_row("MA", 300, t))
    eng.ingest_trade(_clock_row(t + lv.PENDING_S + lv.PENDING_SWEEP_S, 400))
    eng.ingest_trade(_ps_row("MB", 401, t + lv.PENDING_S + lv.PENDING_SWEEP_S))  # the sweep (by the clock) drops MA's print
    assert list(eng._pending) == ["MB"] and eng._pending_n == 1 and eng.counters["pending_expired"] == 1
    monkeypatch.setattr(lv, "PENDING_MAX_PER_MINT", 3)
    monkeypatch.setattr(lv, "PENDING_MAX_TOTAL", 5)
    eng = _tiny_engine()
    for i in range(6):
        eng.ingest_trade(_ps_row("MA", 300 + i, t + i))
    assert eng.counters["pending_overflow"] == 3 and eng._pending_n == 3  # the earliest 3 are kept
    for i in range(3):
        eng.ingest_trade(_ps_row("MB", 310 + i, t + 10 + i))
    assert eng._pending_n <= 5 and "MA" not in eng._pending and list(eng._pending) == ["MB"] and eng.counters["pending_evicted"] == 3
    eng.ingest_migration({"type": "complete", "mint": "MA", "slot": 250, "block_time": t})  # evicted: nothing to replay, no error
    assert len(eng.mints["MA"].prints["slot"]) == 0


# ------------------------------------------------------------------------------------------------------------------------------- restart: exit-on-start and the warm-up gate
def _log_marks(log, eng, results):
    for res in results:
        if res.actions:
            lv.log_actions(log, res, {m.mint: m for m in eng.members()}, res.t * 1000)


def _engine_a_log(tmp_path, n_marks=2):
    """The previous process: engine A decides the first n_marks marks of the scenario and logs them after a start row. Returns (log, engine, results, sha)."""
    from tools.arena_declog import DecisionLog

    sha = "ab" * 20
    log = DecisionLog(tmp_path / "declog")
    log.append(lv.BOT_ID, MARKS[0] * 1000, {"kind": "start", "book": lv.BOOK, "exp": "EXP-027", "mode": "dry", "schema": lv.SCHEMA, "frozen_sha": sha})
    eng, res = drive(event_stream(scenario()), MARKS[:n_marks])
    _log_marks(log, eng, res)
    return log, eng, res, sha


def test_restart_exits_are_written_for_exactly_the_open_entries_and_only_once(tmp_path):
    log, eng, res, _ = _engine_a_log(tmp_path)
    held = {f"{m}:{t0}": m for m, t0 in eng.book.pos.items()}  # the previous process's own book: what it still held at the restart
    rows = log.rows(lv.BOT_ID)
    entries = [r["order_id"] for r in rows if r["kind"] == "entry"]
    exited = {r["order_id"] for r in rows if r["kind"] == "exit"}
    assert len(held) >= 8 and set(entries) - exited == set(held) and exited, "the scenario must leave positions open and have closed some"
    now_ms = (MARKS[1] + 1200) * 1000
    ex = lv.restart_exits(log, lv.BOT_ID, now_ms)
    assert [r["order_id"] for r in ex] == [o for o in entries if o in held]  # entry order, exactly the open ones
    for r in ex:
        assert (r["kind"], r["reason"], r["decision_slot"], r["t_mark"], r["ms"], r["mint"]) == ("exit", "restart", None, MARKS[1], now_ms, held[r["order_id"]])
    assert log.verify(lv.BOT_ID)[1] == len(rows) + len(ex) - 1  # the chain is intact
    n = len(log.rows(lv.BOT_ID))
    assert lv.restart_exits(log, lv.BOT_ID, now_ms + 5000) == [] and len(log.rows(lv.BOT_ID)) == n  # idempotent: nothing left open
    assert lv.open_entries(log) == []
    # t_mark is the mark at or before now: an hour later it is the next mark
    log2, _, _, _ = _engine_a_log(tmp_path / "b")
    assert {r["t_mark"] for r in lv.restart_exits(log2, now_ms=(MARKS[1] + 3700) * 1000)} == {MARKS[1] + 3600}


def test_restart_exits_refuses_a_missing_or_backwards_clock_and_a_broken_chain(tmp_path):
    from tools.arena_declog import LogChainError

    log, _, _, _ = _engine_a_log(tmp_path)
    n = len(log.rows(lv.BOT_ID))
    for bad in (None, 0, True, "123", (MARKS[1] - 1) * 1000):  # the last logged row is at MARKS[1] * 1000
        with pytest.raises(lv.LiveRefused):
            lv.restart_exits(log, lv.BOT_ID, bad)
    with pytest.raises(lv.LiveRefused):
        lv.restart_exits(log, lv.BOT_ID, MARKS[1] * 1000 + 1, reason="")
    assert len(log.rows(lv.BOT_ID)) == n
    f = sorted((tmp_path / "declog" / "decisions" / lv.BOT_ID).glob("*.jsonl"))[0]
    f.write_text(f.read_text().replace('"stake_sol":0.25', '"stake_sol":0.26', 1))
    from tools.arena_declog import DecisionLog

    with pytest.raises(LogChainError):
        lv.restart_exits(DecisionLog(tmp_path / "declog"), lv.BOT_ID, MARKS[1] * 1000 + 1)


def test_host_start_writes_the_start_row_then_the_exits_and_a_first_start_has_none(tmp_path):
    from tools.arena_declog import DecisionLog

    first = DecisionLog(tmp_path / "first")
    out = lv.host_start(first, now_ms=MARKS[0] * 1000, frozen_sha="cd" * 20)
    assert out["exits"] == [] and out["start"]["kind"] == "start" and out["start"]["frozen_sha"] == "cd" * 20 and out["start"]["warmup_s"] == lv.DEFAULT_WARMUP_S
    log, eng, res, sha = _engine_a_log(tmp_path / "prev")
    held = {f"{m}:{t0}" for m, t0 in eng.book.pos.items()}
    out = lv.host_start(log, now_ms=(MARKS[1] + 1200) * 1000, frozen_sha=sha)
    rows = log.rows(lv.BOT_ID)
    assert rows[-len(out["exits"]) - 1]["kind"] == "start" and rows[-len(out["exits"]) - 1]["seq"] == out["start"]["seq"]  # start first, then the exits
    assert {r["order_id"] for r in out["exits"]} == held and all(r["kind"] == "exit" and r["reason"] == "restart" for r in rows[-len(held):])
    again = lv.host_start(log, now_ms=(MARKS[1] + 1300) * 1000, frozen_sha=sha)  # a second restart: a new start row, no exits
    assert again["exits"] == [] and log.rows(lv.BOT_ID)[-1]["seq"] == again["start"]["seq"]
    for bad in ("zz", "ab" * 19, None):
        if bad is not None:
            with pytest.raises(lv.LiveRefused):
                lv.host_start(log, now_ms=(MARKS[1] + 1400) * 1000, frozen_sha=bad)
    with pytest.raises(lv.LiveRefused):
        lv.host_start(log, now_ms=(MARKS[1] + 1400) * 1000, frozen_sha=sha, warmup_s=3600)  # shorter than 52 h without a proof


def test_engine_emits_no_entry_during_the_warmup_and_then_resumes_with_an_empty_book():
    ev = event_stream(scenario())
    ref_eng, ref = drive(ev, MARKS)
    first = min(ref_eng.mkt)
    warm = MARKS[2] - first  # marks 0 and 1 are inside the warm-up; mark 2 is exactly warmup_s after the first market minute
    eng, res = drive(ev, MARKS, warmup_s=warm)
    assert [r.warmup for r in res] == [True, True, False, False] and eng.counters["warmup_marks"] == 2
    assert all(len(r.ranked) >= 10 for r in res[:2]) and all(r.actions == [] for r in res[:2])  # the ranking exists; no entry, no exit
    assert any(a[0] == "entry" for r in ref[:2] for a in r.actions)  # without the gate those marks do enter (the gate matters)
    assert [r.ranked for r in res] == [r.ranked for r in ref] and all(r.X.tobytes() == q.X.tobytes() and r.score.tobytes() == q.score.tobytes() for r, q in zip(res, ref))
    assert res[2].actions == [("entry", m, MARKS[2], k) for k, m in enumerate(res[2].ranked[:10])]  # the book was empty: the whole top 10, ranks 0..9
    assert eng.book.pos and {t0 for t0 in eng.book.pos.values()} <= {MARKS[2], MARKS[3]} and not any(a[0] == "exit" for a in res[2].actions)


def test_a_warmup_of_zero_or_none_is_the_unchanged_decision_path():
    ev = event_stream(scenario())
    _, ref = drive(ev, MARKS)
    for w in (0, None):
        _, res = drive(ev, MARKS, warmup_s=w)
        assert [sig(r) for r in res] == [sig(r) for r in ref] and not any(r.warmup for r in res)
    for bad in (-1, True, 1.5, "52h"):
        with pytest.raises(lv.LiveRefused):
            mk_engine(warmup_s=bad)


def test_the_warmup_gate_stays_open_once_a_mark_has_passed_it():
    ev = event_stream(scenario())
    eng, i = mk_engine(warmup_s=0), 0
    i = feed(eng, ev, i, MARKS[0] + 30)
    assert eng.decide(MARKS[0], w0=W0, last_entry=E - 3600).warmup is False and eng._warm_done
    eng.warmup_s = 10 ** 9  # even if a caller raised the bar, a mark that has passed does not re-close the gate (positions may be held)
    i = feed(eng, ev, i, MARKS[1] + 30)
    assert eng.decide(MARKS[1], w0=W0, last_entry=E - 3600).warmup is False


def test_new_host_engine_defaults_to_52_hours_and_a_shorter_one_needs_a_proof_reference():
    assert lv.DEFAULT_WARMUP_S == 48 * 3600 + 4 * 3600 == lv.GRID_HI_S + 4 * lv.MARK_S
    eng = lv.new_host_engine(universe_min_mbt=BT0, pda_fn=pda_of, hash_fn=lv.DuckHash(), score_fn=_score_ret)
    assert eng.warmup_s == lv.DEFAULT_WARMUP_S
    with pytest.raises(lv.LiveRefused):
        lv.new_host_engine(universe_min_mbt=BT0, warmup_s=3600)
    with pytest.raises(lv.LiveRefused):
        lv.new_host_engine(universe_min_mbt=BT0, warmup_s=3600, shorter_warmup_proof="  ")
    assert lv.new_host_engine(universe_min_mbt=BT0, warmup_s=3600, shorter_warmup_proof="md5-proof-ref").warmup_s == 3600
    assert lv.new_host_engine(universe_min_mbt=BT0, warmup_s=lv.DEFAULT_WARMUP_S + 1).warmup_s == lv.DEFAULT_WARMUP_S + 1  # longer needs nothing
    with pytest.raises(lv.LiveRefused):
        lv.new_host_engine(universe_min_mbt=BT0, mode="live")  # the live refusal is the engine's, unchanged


# ------------------------------------------------------------------------------------------------------------------------------- feed gaps in the declog
def test_log_gap_writes_a_gap_row_for_a_gap_that_halts_buys_and_nothing_otherwise(tmp_path):
    from tools.arena_declog import DecisionLog
    from tools.r4b_feed_adapter import GapRecord, Mapped, Reject

    log = DecisionLog(tmp_path / "g")
    log.append(lv.BOT_ID, 1_000_000, {"kind": "start", "book": lv.BOOK, "schema": lv.SCHEMA, "frozen_sha": "ab" * 20})
    lossy = GapRecord("data_loss", "pumpswap", 2_000_000, 1_900_000, 2_000_000, True, True)
    row = lv.log_gap(log, lossy)
    assert (row["kind"], row["ms"], row["gap_kind"], row["from_ms"], row["to_ms"], row["data_loss"]) == ("gap", 2_000_000, "data_loss", 1_900_000, 2_000_000, True)
    assert "instant_ms" not in row
    n = len(log.rows(lv.BOT_ID))
    reconnect = GapRecord("socket_reconnect", "pumpfun", 2_100_000, 2_050_000, 2_100_000, False, True)  # no data lost: halts_buys is False
    assert reconnect.halts_buys is False and lv.log_gap(log, reconnect) is None and lv.log_gap(log, Mapped("gap", reconnect)) is None and len(log.rows(lv.BOT_ID)) == n
    unknown = GapRecord("gap", None, 2_200_000, None, None, None, None)  # data_loss unknown: fail closed
    assert unknown.halts_buys and lv.log_gap(log, Mapped("gap", unknown))["gap_kind"] == "gap"
    rej = lv.log_gap(log, Mapped("reject", Reject("unknown_type", "x", None)), now_ms=2_300_000)
    assert (rej["gap_kind"], rej["ms"], rej["from_ms"], rej["to_ms"]) == ("reject_unknown_type", 2_300_000, None, None)
    assert lv.log_gap(log, Mapped("reject", Reject("unresolved_expired", "trade", None, 3, halts_buys=False)), now_ms=2_400_000) is None
    assert log.verify(lv.BOT_ID)[1] == len(log.rows(lv.BOT_ID)) - 1


def test_log_gap_instant_fallbacks_and_a_late_log_keep_the_log_in_order(tmp_path):
    from tools.arena_declog import DecisionLog
    from tools.r4b_feed_adapter import GapRecord

    log = DecisionLog(tmp_path / "g")
    log.append(lv.BOT_ID, 5_000_000, {"kind": "start", "book": lv.BOOK, "schema": lv.SCHEMA, "frozen_sha": "ab" * 20})
    no_instant = GapRecord("data_loss", None, None, None, None, True, None)
    with pytest.raises(lv.LiveRefused):
        lv.log_gap(log, no_instant)
    assert lv.log_gap(log, no_instant, now_ms=5_100_000)["ms"] == 5_100_000
    old = lv.log_gap(log, GapRecord("data_loss", None, 4_000_000, 3_900_000, 4_000_000, True, None))  # its instant is behind the newest row
    assert old["ms"] == 5_100_000 and old["instant_ms"] == 4_000_000 and old["to_ms"] == 4_000_000  # ms raised to keep the file order; the true instant is kept
    assert [r["ms"] for r in log.rows(lv.BOT_ID)] == sorted(r["ms"] for r in log.rows(lv.BOT_ID))
    assert log.verify(lv.BOT_ID)[1] == 2  # start, the now_ms gap, the late gap; the refused call wrote nothing
    with pytest.raises(lv.LiveRefused):
        lv.log_gap(log, GapRecord("data_loss", None, True, None, None, True, None))  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------------------------------------- frozen_sha must describe the running code
def _git(repo, *a):
    import subprocess

    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_repo_head_sha_refuses_modified_tracked_files_and_ignores_untracked_ones(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "c1")
    head = _git(repo, "rev-parse", "HEAD")
    assert lv.repo_head_sha(repo) == head  # clean: the sha
    (repo / "scratch.txt").write_text("not tracked\n")
    assert lv.repo_head_sha(repo) == head  # an untracked file does not change the running code
    (repo / "a.py").write_text("x = 2\n")
    with pytest.raises(lv.LiveRefused, match="modified tracked"):
        lv.repo_head_sha(repo)  # a modified tracked file: refused, and not cached from the clean call
    _git(repo, "add", "a.py")
    with pytest.raises(lv.LiveRefused, match="modified tracked"):
        lv.repo_head_sha(repo)  # staged is still not HEAD
    _git(repo, "commit", "-q", "-m", "c2")
    head2 = _git(repo, "rev-parse", "HEAD")
    assert head2 != head and lv.repo_head_sha(repo) == head2
    (repo / "a.py").unlink()
    with pytest.raises(lv.LiveRefused, match="modified tracked"):
        lv.repo_head_sha(repo)  # a deleted tracked file counts too
    with pytest.raises(lv.LiveRefused):
        lv.repo_head_sha(tmp_path / "not-a-repo")  # no guess when git cannot answer


def test_an_explicit_frozen_sha_never_reaches_the_dirty_check(tmp_path, monkeypatch):
    from tools.arena_declog import DecisionLog

    def boom(*a, **k):
        raise AssertionError("repo_head_sha must not be called when frozen_sha is explicit")

    monkeypatch.setattr(lv, "repo_head_sha", boom)
    out = lv.host_start(DecisionLog(tmp_path / "d"), now_ms=MARKS[0] * 1000, frozen_sha="ab" * 20)
    assert out["start"]["frozen_sha"] == "ab" * 20
    monkeypatch.setattr(lv, "repo_head_sha", lambda *a, **k: (_ for _ in ()).throw(lv.LiveRefused("dirty")))
    with pytest.raises(lv.LiveRefused, match="dirty"):
        lv.host_start(DecisionLog(tmp_path / "d2"), now_ms=MARKS[0] * 1000)  # the default sha path does consult it, and its refusal stops the start
    assert not (tmp_path / "d2" / "decisions").exists()  # nothing was written
