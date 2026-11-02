"""HV7's share of the tests owed by QP-RULING-1102-tip-shared-slot R4 (sha256 9237804998b520ccafac5605b3564324f25bf0fa8f49955381d9675a38a60b20): T1, T2 and T3.
T4 is HV4-T only and T5 is s1t only (not here). SYNTHETIC rows only; no tape, tip file, ledger or sealed path is opened. The follower, writer, adapter and engine bytes are unchanged:
these tests pin the behaviour the ruling's section 3 and 4 rely on, so a later change shows up.

  T1 (F2, cache priming)  a CreatePool tx primes the follower's pool cache, so later trades of that pool carry tx_index; an uncached pool's trades come back unresolved
                          (no tx_index) once, after which the pool is cached.
  T2 (F3, FIFO)          `LRUPools` evicts by insertion order: a decoder-path hit (`in`, `[]`) does not save a pool, so a migrated pool leaves only after `cap` later insertions.
  T3 (F4, engine reads)  the entry and exit prices of a position are bit-identical when the two signatures of a no-tx_index group far from its boundaries are swapped; the same
                          group at the landing boundary changes the entry (the control: the test can see the order).
Nothing here says HV7 makes money.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("MAL_ARENA_TEST", "1")

from observe.trade_decode import b58decode, b58encode  # noqa: E402
from tools import fast_tip_follower as F  # noqa: E402
from tools import test_arena_tip_adapter as TA  # noqa: E402
from tools import test_arena_tip_equiv as TE  # noqa: E402
from tools.arena_paper import register_bot  # noqa: E402
from tools.pump_history_backfill import resolve_unresolved, rows_from_block  # noqa: E402

eq = TE.eq
FIXTURES = Path(__file__).resolve().parents[1] / "observe" / "fixtures"      # observe.fake_ws needs `websockets`, which the test venv lacks: the same two lines inline


def fixture_line(name: str) -> str:
    return f"Program data: {(FIXTURES / name).read_text(encoding='utf-8').strip()}"
WSOL = TA.WSOL


# ------------------------------------------------------------------------------------------------------------------------ T2
def test_t2_lrupools_is_fifo_in_effect_a_decoder_hit_does_not_save_a_pool():
    c = F.LRUPools(cap=3)
    for k in "ABC":
        c[k] = (k, WSOL)
    assert "A" in c and c["A"] == ("A", WSOL)                 # the decoder's path: `in` and `[]`
    c["D"] = ("D", WSOL)
    assert "A" not in c and list(c) == ["B", "C", "D"]        # A, the oldest insertion, is gone although it was just read
    c2 = F.LRUPools(cap=3)
    for k in "ABC":
        c2[k] = (k, WSOL)
    assert c2.get("A") == ("A", WSOL)                         # only `get` moves the recency (the follower's resolver path), which the decoder does not use
    c2["D"] = ("D", WSOL)
    assert "A" in c2 and "B" not in c2
    assert F.LRUPools().cap == 200_000                        # the cap R2 (b) quotes (tripwire at 180,000 = 90%)


def test_t2_the_decoder_reads_the_cache_through_in_and_getitem_only():
    src = (Path(__file__).resolve().parents[1] / "observe" / "trade_decode.py").read_text()
    body = src[src.index("def records_from_logs"):]
    assert "pool_mints.get(" not in body and "pool_mints.move_to_end" not in body            # a later change to a recency-moving read would make the cache a true LRU


# ------------------------------------------------------------------------------------------------------------------------ T1
POOL = "HwK2JkkHc5Ekt6umApmj5RerhugNSiMULioThvKvkGB9"                # the pool address inside observe/fixtures/pumpswap_buy_event.b64
MINT = b58encode(bytes([9]) * 32)


def pd(raw: bytes) -> str:
    import base64

    return "Program data: " + base64.b64encode(raw).decode()


def create_pool_log(pool: str = POOL, mint: str = MINT, quote: str = WSOL) -> str:
    raw = bytearray(bytes.fromhex("b1310cd2a076a774") + bytes(300))        # the CreatePoolEvent discriminator, then the fields the decoder reads
    raw[173:205], raw[50:82], raw[82:114] = b58decode(pool), b58decode(mint), b58decode(quote)
    return pd(bytes(raw))


def tx(sig: str, *logs: str) -> dict:
    return {"meta": {"err": None, "logMessages": list(logs)}, "transaction": {"signatures": [sig]}}


def block(slot: int, *txs: dict) -> dict:
    return {"slot": slot, "blockTime": 1_790_318_932 + slot, "transactions": list(txs)}


def test_t1_a_createpool_primes_the_cache_and_an_uncached_pool_misses_once():
    buy = sell = fixture_line("pumpswap_buy_event.b64")                                     # two txs trading the same pool (the sell fixture is another pool)
    # (a) a migration-style block: the CreatePool tx comes first, then two txs trade the new pool; both trades carry tx_index and nothing is unresolved
    cache = F.LRUPools()
    out = rows_from_block(block(100, tx("A" * 88, create_pool_log()), tx("B" * 88, buy), tx("C" * 88, sell)), cache)
    assert out["unresolved"] == [] and [r["tx_index"] for r in out["trades"]] == [1, 2]
    assert POOL in cache                                                                    # F2: the pool is cached by its own creation, before its trades are decoded
    # (b) the pool is not cached: two txs on it give two rows WITHOUT tx_index (the shared-slot case) ...
    cold = F.LRUPools()
    out = rows_from_block(block(200, tx("D" * 88, buy), tx("E" * 88, sell)), cold)
    assert out["trades"] == [] and len(out["unresolved"]) == 2
    ready, dropped = resolve_unresolved(out["unresolved"], cold, 1_790_319_132, lambda pools: {p: (MINT, WSOL) for p in pools})
    assert dropped == 0 and len(ready) == 2 and all("tx_index" not in r for r in ready)     # resolved after the block: the follower emits them without tx_index
    assert POOL in cold                                                                     # ... and the lookup cached the pool (`pool_mints.update`)
    # (c) the next block on that pool: cache hit, so its rows carry tx_index
    out = rows_from_block(block(201, tx("F" * 88, buy), tx("G" * 88, sell)), cold)
    assert out["unresolved"] == [] and [r["tx_index"] for r in out["trades"]] == [0, 1]


# ------------------------------------------------------------------------------------------------------------------------ T3
SD, SE, LAST = 30, 50, 80                                   # decision slot, exit-order slot, last slot of the stream
POOL3 = "P0"


def prow(slot: int, sig: str, q: int, *, tx_index: int | None, side: str = "buy") -> dict:
    w = TA.wt(slot, 0, venue="pumpswap", pool=POOL3, tx=0, side=side, signature=sig, quote_reserve=q * 10**9, base_reserve=206 * 10**12, sol_lamports=10**8,
              token_raw=10**9)
    if tx_index is None:
        del w["tx_index"]
    else:
        w["tx_index"] = tx_index
    return eq.engine_row(eq.twin("trade", w))


def stream3(group_slot: int, swap: bool) -> list[dict]:
    """One print per slot 1..LAST with a distinct pre-trade reserve each, except `group_slot`, which holds a 2-signature group without tx_index (two different reserves)."""
    rows = []
    for s in range(1, LAST + 1):
        if s == group_slot:
            a, b = ("zzz", "aaa") if swap else ("aaa", "zzz")          # the engine's receive form sorts a no-tx group by signature: swapping the names swaps the order
            rows += [prow(s, a, 90 + s, tx_index=None, side="buy"), prow(s, b, 140 + s, tx_index=None, side="sell")]
        else:
            rows.append(prow(s, f"s{s}", 60 + s, tx_index=0))
    return rows


def priced_md5(tmp: Path, group_slot: int, swap: bool) -> tuple[str, int]:
    """Entry decided after the row of slot SD, exit ordered after SE; md5 of the priced `trade` / `excluded` ledger rows and how many there are."""
    tmp.mkdir(parents=True)
    eng = TA.mk_engine(tmp)
    register_bot("t3", "abc1234", 0, str(tmp / "bots.json"), deciding_stake=0.25, model_ids=[], now_ms=0, verify_sha=False)
    pid = None
    for r in stream3(group_slot, swap):
        eng.on_row(r)
        if pid is None and int(r["slot"]) == SD:
            pid = eng.buy("t3", POOL3, 0.25, int(eng.hw_ms or 0), int(eng.hw_slot), {"rule": "t3"})
        if pid is not None and int(r["slot"]) == SE and int(eng.hw_slot) == SE:
            eng.sell("t3", pid, 1.0, int(eng.hw_ms or 0), int(eng.hw_slot), "hold")
    h, n = hashlib.md5(), 0
    for p in sorted((tmp / "ledger").rglob("*.jsonl")):
        for line in p.read_text().splitlines():
            r = json.loads(line)
            if r.get("kind") in ("trade", "excluded"):
                n += 1
                h.update(json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    return h.hexdigest(), n


def test_t3_a_shared_slot_group_far_from_the_reads_does_not_change_the_priced_position(tmp_path):
    a, na = priced_md5(tmp_path / "a", 5, False)
    b, nb = priced_md5(tmp_path / "b", 5, True)
    assert na >= 1 and nb == na                               # not vacuous: the position was priced
    assert a == b                                             # entry (Qe, Be), exit (Qx, Bx) and the guard spot are bit-identical when the group's signatures are swapped
    c, _ = priced_md5(tmp_path / "c", 60, False)
    d, _ = priced_md5(tmp_path / "d", 60, True)
    assert c == d                                             # a group after the exit's landing, before no read, is also invisible


def test_t3_control_the_same_group_at_the_landing_boundary_changes_the_entry(tmp_path):
    """The group is moved slot by slot across the landing window after the decision; at least one placement must make the swap change the priced position, otherwise the test above
    could not see the order at all."""
    changed = []
    for g in range(SD + 1, SD + 8):
        a, _ = priced_md5(tmp_path / f"a{g}", g, False)
        b, _ = priced_md5(tmp_path / f"b{g}", g, True)
        if a != b:
            changed.append(g)
    assert changed, "no placement of the group near the landing boundary changed the priced position: the T3 test cannot see the order"
    assert all(SD < g <= SD + 7 for g in changed)
