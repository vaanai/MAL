"""P3 (QP-RULING-1107-tip-migration-crossbook): equivalence of the old and the new follower output downstream. SYNTHETIC only: tmp dirs, no tip tape, derived
copy, network or SSH.

  /data/mal/venv/bin/python -m pytest -q tools/test_tip_migration_p3_equiv.py tools/test_tip_follower_inner_migration.py

OLD = the follower of c658b51f (log-path migration rows only); NEW = this tree's follower (adds inner_event migration rows). Both run over the same scripted
blocks. One block carries a migration whose logs are cut (event only in the inner instructions): OLD misses it, NEW adds it.

(a) the arena tip adapter (the writer's row mapping) over both outputs: byte-identical except the one added pool_create row.
(b) tools/r4bt_c0_twin.py: NOT run here. Its run() needs a verified zst tape dir, the pinned engine and module blobs, a registered W sha and an order ledger; none
    can be built synthetically without faking them. test_twin_consumes_only_the_arena_rows_not_follower_files shows the structural reason instead.
(c) r4b Engine: decisions equal when the logs are whole; and a control showing the decisions DO react to a missing migration row (the test is not vacuous).
"""

from __future__ import annotations

import hashlib
import json
import random
import zlib
from pathlib import Path

import pytest

from tools import arena_tip_adapter as ata
from tools import fast_tip_follower as ftf
from tools import r4b_live as lv
from tools.test_tip_follower_inner_migration import base_module, files, kind, run_follower, script_blocks

REPO = Path(__file__).resolve().parent.parent
INNER_ONLY_SIG = "sigM1"
SLOTS_BROKEN = (100, 101, 103, 105)  # M0 log, M1 logs cut + inner event, M3 log AND inner (guard), a complete
SLOTS_WHOLE = (100, 103, 105)  # no inner-only migration


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def blocks_of(slots) -> dict:
    b = script_blocks()
    return {s: b[s] for s in slots}


def rows_of(blob: bytes) -> list[dict]:
    return [json.loads(x) for x in blob.splitlines()]


def adapter_items(fmap: dict[str, bytes]) -> list[tuple]:
    """The reader's items for a follower output: trades and migration/complete rows in slot order (the same ordering rule for both variants)."""
    items = []
    for name, blob in sorted(fmap.items()):
        k = kind(name)
        if k in ("trades", "migrations"):
            items += [("trade" if k == "trades" else "migration", r) for r in rows_of(blob)]
    items.sort(key=lambda it: (it[1]["slot"], 0 if it[0] == "migration" else 1))  # a complete / pool_create comes BEFORE its slot's trades
    return items


def adapt(fmap: dict[str, bytes]) -> list[dict]:
    out = ata.ArenaTipAdapter().feed(adapter_items(fmap))
    assert not any(r["type"] == "gap" for r in out)  # no gap row: the diff below is rows only
    return out


def dump(rows: list[dict]) -> list[bytes]:
    return [json.dumps(r, sort_keys=True, separators=(",", ":")).encode() for r in rows]


@pytest.fixture(scope="module")
def broken(tmp_path_factory):
    po, pn = tmp_path_factory.mktemp("p3old"), tmp_path_factory.mktemp("p3new")
    run_follower(base_module(), po, blocks_of(SLOTS_BROKEN))
    run_follower(ftf, pn, blocks_of(SLOTS_BROKEN))
    return files(po), files(pn)


@pytest.fixture(scope="module")
def whole(tmp_path_factory):
    po, pn = tmp_path_factory.mktemp("p3wo"), tmp_path_factory.mktemp("p3wn")
    run_follower(base_module(), po, blocks_of(SLOTS_WHOLE))
    run_follower(ftf, pn, blocks_of(SLOTS_WHOLE))
    return files(po), files(pn)


def mig_file(fmap: dict[str, bytes]) -> list[dict]:
    return rows_of(next(v for k, v in fmap.items() if kind(k) == "migrations"))


# ------------------------------------------------------------------------------------------------------------------------------------ (a) adapter / writer
def test_a_follower_variants_differ_by_exactly_one_inner_event_migration(broken):
    fo, fn = broken
    old, new = mig_file(fo), mig_file(fn)
    added = [r for r in new if r.get("event_source") == "inner_event"]
    assert [r["signature"] for r in added] == [INNER_ONLY_SIG] and len(new) == len(old) + 1
    assert INNER_ONLY_SIG not in {r["signature"] for r in old}  # OLD really misses it
    assert [r for r in new if r.get("event_source") != "inner_event"] == old


def test_a_adapter_output_equal_except_the_added_pool_create_row(broken):
    fo, fn = broken
    out_o, out_n = adapt(fo), adapt(fn)
    extra = [r for r in out_n if r["type"] == "pool_create" and r["signature"] == INNER_ONLY_SIG]
    assert len(extra) == 1
    rest = [r for r in out_n if r is not extra[0]]
    assert dump(rest) == dump(out_o)  # byte-identical, same order
    assert len(out_n) == len(out_o) + 1
    assert md5(b"\n".join(dump(rest))) == md5(b"\n".join(dump(out_o)))
    assert {r["type"] for r in out_o} >= {"trade", "complete", "pool_create"}  # the fixture exercises all three kinds


def test_a_added_row_carries_the_same_fields_as_a_log_path_row(broken):
    _fo, fn = broken
    pcs = [r for r in adapt(fn) if r["type"] == "pool_create"]
    logp = next(r for r in pcs if r["signature"] == "sigM0")
    inner = next(r for r in pcs if r["signature"] == INNER_ONLY_SIG)
    assert set(inner) == set(logp)  # same keys
    for k in ("v", "type", "prog", "quote_mint"):
        assert inner[k] == logp[k], k
    assert inner["prog"] == "pumpswap" and inner["v"] == 1
    assert isinstance(inner["tx_index"], int) and isinstance(inner["block_time"], int) and inner["pool"] and inner["mint"] and inner["event_index"] is not None
    # event_source (follower side only) and init_boost never reach the arena row: the one legitimate difference is gone at the adapter
    assert "event_source" not in inner and "init_boost" not in inner
    # on the follower side the added row differs from a log row only by event_source / init_boost
    fm = mig_file(fn)
    f_log = next(r for r in fm if r["signature"] == "sigM0")
    f_in = next(r for r in fm if r["signature"] == INNER_ONLY_SIG)
    assert set(f_in) - set(f_log) <= {"event_source", "init_boost"} and set(f_log) <= set(f_in)


def test_a_log_plus_inner_tx_is_one_pool_create(broken):
    _fo, fn = broken
    assert [r["signature"] for r in adapt(fn) if r["type"] == "pool_create"].count("sigM3") == 1


def test_a_whole_logs_adapter_output_is_identical(whole):
    fo, fn = whole
    assert fo == fn  # every file, byte for byte
    assert dump(adapt(fo)) == dump(adapt(fn)) and len(adapt(fo)) > 0


# ------------------------------------------------------------------------------------------------------------------------------------ (b) C0 twin
def test_twin_consumes_only_the_arena_rows_not_follower_files():
    """(b) is not run synthetically (see the module docstring). What can be shown: the twin reads tape-*.jsonl.zst of the arena writer's derived copy, so the only
    follower-side difference that can reach it is the added pool_create row proven in (a)."""
    src = (REPO / "tools" / "r4bt_c0_twin.py").read_text()
    assert "tape-" in src and ".jsonl.zst" in src
    assert "migrations-" not in src and "fast_tip_follower" not in src and "event_source" not in src


# ------------------------------------------------------------------------------------------------------------------------------------ (c) r4b Engine
BT0 = 1_790_000_000 // 3600 * 3600
S0 = 438_000_000
V = 17_600_000_000
WSOL = lv.rl.WSOL_MINT
W0, E = BT0 + 2 * 3600, BT0 + 4 * 3600


def pda_of(m: str) -> str:
    return "PDA_" + m


def hash_of(s) -> int:
    return zlib.crc32(str(s).encode())


def slot_of(bt: int) -> int:
    return S0 + (bt - BT0) * 5 // 2


def mini_scenario(seed: int = 5, n: int = 5) -> list[tuple]:
    """(arrival bt, order, kind, row): creates, complete+migration pairs and PumpSwap prints for n mints over 4 hours, walker row shape."""
    rnd, ev = random.Random(seed), []
    for k in range(n):
        mint, bt_c = f"M{k}", BT0 + 1000 + 151 * k
        ev.append((bt_c - 900, 0, "create", {"type": "create", "mint": mint, "creator": f"CR{k}", "is_mayhem_mode": False, "slot": slot_of(bt_c - 900),
                                             "block_time": bt_c - 900}))
        ev.append((bt_c, 1, "migration", {"type": "complete", "mint": mint, "slot": slot_of(bt_c), "block_time": bt_c, "tx_index": 3, "event_index": 0}))
        ev.append((bt_c, 1, "migration", {"type": "migration", "mint": mint, "slot": slot_of(bt_c), "block_time": bt_c, "tx_index": 3, "event_index": 1,
                                          "pool": pda_of(mint), "quote_mint": WSOL}))
        q, b, bt, i = 85 * 10**9, 206 * 10**12, bt_c + 3, 0
        while bt < E + 20:
            buy = rnd.random() < 0.55
            sol = rnd.randint(5 * 10**7, 2 * 10**9)
            tok = int(b * sol / (q + V + sol)) if buy else int(b * rnd.uniform(0.001, 0.02))
            if not buy:
                sol = int((q + V) * tok / (b + tok))
            row_q, row_b = q, b
            q, b = (q + sol, b - tok) if buy else (q - sol, b + tok)
            ev.append((bt, 2, "trade", {"venue": "pumpswap", "mint": mint, "trader": f"TR{rnd.randint(0, 20)}", "side": "buy" if buy else "sell", "sol_lamports": sol,
                                        "token_raw": tok, "quote_reserve": row_q, "base_reserve": row_b, "pool": pda_of(mint), "slot": slot_of(bt),
                                        "signature": f"s{mint}{i}", "event_index": 0, "tx_index": i % 50, "block_time": bt, "virtual_quote_reserves": V}))
            i += 1
            bt += rnd.randint(8, 40 + 20 * k)
    ev.sort(key=lambda e: (e[0], e[1]))
    return ev


def decisions(ev: list[tuple]) -> tuple[str, int, list]:
    """The live call pattern: feed up to T + 30 s, decide T. Returns (md5 of the decision list, n ranked mints over all marks, per-mark mints)."""
    eng = lv.Engine(universe_min_mbt=BT0, pda_fn=pda_of, hash_fn=hash_of, score_fn=lambda b, X: X[:, 0])
    i, h, ranked, mints = 0, hashlib.md5(), 0, []
    for T in range(W0, E, 3600):
        while i < len(ev) and ev[i][0] < T + 30:
            {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[ev[i][2]](ev[i][3])
            i += 1
        r = eng.decide(T, w0=W0, last_entry=E - 3600)
        for part in (r.t, r.sd, list(r.mints), r.sdv.tobytes(), r.X.tobytes(), r.score.tobytes(), r.ranked, r.actions, r.d3_dropped):
            h.update(repr(part).encode())
        ranked += len(r.mints)
        mints.append(list(r.mints))
    return h.hexdigest(), ranked, mints


def test_c_engine_decisions_equal_when_logs_are_whole(whole):
    fo, fn = whole
    assert fo == fn  # the follower's output for whole logs is byte-equal, so the Engine's input is
    ev = mini_scenario()
    d_old, n_old, _ = decisions(ev)
    ev_new = [(a, o, k, json.loads(json.dumps(r))) for a, o, k, r in ev]  # through the same JSON bytes the follower writes
    d_new, n_new, _ = decisions(ev_new)
    assert d_old == d_new and n_old == n_new and n_old > 0


def test_c_engine_ignores_a_duplicate_inner_row_of_a_logged_migration():
    """Defence in depth: even if the double-count guard failed, a second row of the same migration (same pool) changes no decision."""
    ev = mini_scenario()
    dup = [(a, 1, "migration", dict(r, event_source="inner_event")) for a, o, k, r in ev if k == "migration" and r["type"] == "migration"]
    assert decisions(sorted(ev + dup, key=lambda e: (e[0], e[1])))[0] == decisions(ev)[0]


def test_c_control_a_missing_migration_row_changes_the_decisions():
    """Non-vacuity: the OLD gap (one migration missing) IS visible to the Engine, which is why the added row matters; equality above is not a trivial pass."""
    ev = mini_scenario()
    old = [e for e in ev if not (e[2] == "migration" and e[3]["type"] == "migration" and e[3]["mint"] == "M4")]
    d_full, _n, m_full = decisions(ev)
    d_old, _n2, m_old = decisions(old)
    assert d_full != d_old
    assert any("M4" in m for m in m_full) and not any("M4" in m for m in m_old)
