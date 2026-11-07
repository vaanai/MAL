"""Synthetic fault hunt: does the fast tip follower drop pump migration events whose `Program data` log line is absent?

Scope: QP-RULING-1107-r4bt-p0-fail section 4(c) (sha256 1e3b2d09...). TESTS ONLY. Nothing here edits or restarts the follower, the backfill decoder or any r4b module.
Inputs: code at the checkout, and synthetic getBlock JSON built below. No tip tape, derived copy, declog, host out/, network, SSH or job view is touched; every file
written goes under pytest's tmp_path.

Hypothesis. tools/fast_tip_follower.py (_emit) calls `rows_from_block(block, self.pool_cache, FEED)` with `event_v` at its default False. In that legacy mode
tools/pump_history_backfill.rows_from_block takes migration rows only from `lifecycle_from_logs` (a `Program data:` log line with disc bde95db95c94ea94). The
inner-instruction (Anchor emit_cpi) fallback, event_source "inner_event", sits under `if event_v:`. So a migrate tx whose logs were cut at 10 KB, or whose event
is emitted only by self-CPI, gives no migration row on the tip tape; the r4b Engine then has no `mev_pool`/`mev_quote` for the mint and `Engine.member` is None.

The fixtures are written in the shape of the real code (account keys, logMessages, meta.innerInstructions with programIdIndex + base58 `data` = emit_cpi tag + event
blob), so a "legacy 0" below is a property of the decoder path and not of the fixture. What is NOT measured: any real tx, any real tape, the real size of a migrate
tx's logs, whether pump emits the event by CPI only (this file builds both cases by hand).

  /data/mal/venv/bin/python -m pytest tools/test_tip_migration_gap.py -q
"""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from observe.trade_decode import ANCHOR_EVENT_IX_TAG, PUMP_BONDING_PROGRAM, PUMPSWAP_PROGRAM, WSOL_MINT, b58encode
from tools import fast_tip_follower as ftf
from tools import r4b_feed_adapter as fa
from tools import r4b_host_glue as hg
from tools import r4b_live as lv
from tools import r4b_tip_reader as tr
from tools.pump_history_backfill import decode_migration_event, rows_from_block

MIGRATE_DISC = bytes.fromhex("bde95db95c94ea94")
COMPLETE_DISC = bytes.fromhex("5f72619cd42e9808")
HOUR0 = int(datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc).timestamp())  # an hour mark (multiple of 3600)
HOUR_KEY = "2026-11-01T06"
FOLLOWER_FEED = ftf.FEED  # the feed argument the follower passes
MIG_KEYS = ("mint", "type", "slot", "block_time", "tx_index", "event_index", "pool", "quote_mint")  # what Engine.ingest_migration reads


# ------------------------------------------------------------------------------------------------------------------------------ fixture builders
def pk(n: int) -> bytes:
    return bytes([n]) * 32


def key(n: int) -> str:
    return b58encode(pk(n))


def mint_of(i: int) -> str:
    return key(10 + i)


def pool_of(i: int) -> str:
    return key(110 + i)


def u64(x: int) -> bytes:
    return int(x).to_bytes(8, "little")


def i64(x: int) -> bytes:
    return int(x).to_bytes(8, "little", signed=True)


def migration_blob(i: int, ts: int, *, extra_field_before_ts: bytes = b"", tail: bytes = b"") -> bytes:
    """CompletePumpAmmMigrationEvent, the 200-byte layout decode_migration_event reads:
    disc | user | mint | mint_amount | sol_amount | pool_migration_fee | bonding_curve | timestamp | pool | quote_mint. Quote = zero pubkey (native SOL -> WSOL)."""
    return MIGRATE_DISC + pk(1) + pk(10 + i) + u64(50) + u64(2_000_000_000) + u64(100) + pk(60 + i) + extra_field_before_ts + i64(ts) + pk(110 + i) + bytes(32) + tail


def complete_blob(i: int, ts: int) -> bytes:
    """pump bonding CompleteEvent (the `complete` row source): disc | user | mint | bonding_curve | timestamp | quote_mint."""
    return COMPLETE_DISC + pk(1) + pk(10 + i) + pk(60 + i) + i64(ts) + bytes(32)


def data_line(blob: bytes) -> str:
    return "Program data: " + base64.b64encode(blob).decode()


def inner_cpi(blob: bytes, program_index: int = 1) -> dict:
    """One inner instruction as getBlock jsonParsed-off returns it: programIdIndex + base58 data = emit_cpi tag (e445a52e51cb9a1d) + event blob."""
    return {"programIdIndex": program_index, "accounts": [], "data": b58encode(ANCHOR_EVENT_IX_TAG + blob)}


def tx(sig: str, logs: list[str], inner: list[dict] | None = None) -> dict:
    keys = [key(1), PUMP_BONDING_PROGRAM, PUMPSWAP_PROGRAM, key(2)]  # fee payer, pump (index 1), pump_amm (index 2), other
    return {
        "transaction": {"signatures": [sig], "message": {"accountKeys": keys, "instructions": []}},
        "meta": {"err": None, "logMessages": logs, "innerInstructions": [{"index": 0, "instructions": inner}] if inner else []},
    }


def short_logs(blob: bytes | None) -> list[str]:
    out = [f"Program {PUMP_BONDING_PROGRAM} invoke [1]", "Program log: Instruction: Migrate"]
    if blob is not None:
        out.append(data_line(blob))
    out.append(f"Program {PUMP_BONDING_PROGRAM} success")
    return out


def truncated_logs() -> list[str]:
    """Logs cut at the 10 KB cap: the leading lines survive, the event line does not, the last line is "Log truncated"."""
    lines = [f"Program {PUMP_BONDING_PROGRAM} invoke [1]", "Program log: Instruction: Migrate"]
    lines += [f"Program log: filler {n:04d} " + "x" * 80 for n in range(100)]  # ~10 KB of earlier lines
    assert sum(len(x) for x in lines) > 9_500
    lines.append("Log truncated")
    return lines


def block(slot: int, txs: list[dict], block_time: int | None = None) -> dict:
    return {"slot": slot, "blockTime": HOUR0 - 100 + slot if block_time is None else block_time, "parentSlot": slot - 1, "transactions": txs}


def legacy(blk: dict) -> dict:
    """Exactly the follower's call (tools/fast_tip_follower.py _emit): rows_from_block(block, pool_cache, FEED), event_v False."""
    return rows_from_block(blk, {}, FOLLOWER_FEED)


def event_v(blk: dict) -> dict:
    return rows_from_block(blk, {}, FOLLOWER_FEED, event_v=True)


def migrate_block(variant: str, i: int = 0, slot: int = 1_000, tx_pos: int = 3) -> dict:
    """One block, tx at index tx_pos holds the migrate tx; txs before it are unrelated no-event txs so tx_index is non-trivial."""
    blob = migration_blob(i, HOUR0 - 90)
    if variant == "logs":  # F1
        t = tx(f"sigM{i}", short_logs(blob))
    elif variant == "truncated_inner":  # F2
        t = tx(f"sigM{i}", truncated_logs(), [inner_cpi(blob)])
    elif variant == "cpi_only_logs_intact":  # F3
        t = tx(f"sigM{i}", short_logs(None), [inner_cpi(blob)])
    else:
        raise AssertionError(variant)
    filler = [tx(f"sigF{n}", [f"Program {PUMP_BONDING_PROGRAM} invoke [1]", f"Program {PUMP_BONDING_PROGRAM} success"]) for n in range(tx_pos)]
    return block(slot, filler + [t])


def mig_rows(res: dict) -> list[dict]:
    return [r for r in res["migrations"] if r["type"] == "migration"]


# ------------------------------------------------------------------------------------------------------------------------------ F1 .. F4
def test_f1_logs_intact_legacy_gives_one_migration_row():
    rows = mig_rows(legacy(migrate_block("logs")))
    assert len(rows) == 1
    r = rows[0]
    assert (r["mint"], r["pool"], r["quote_mint"], r["slot"], r["tx_index"], r["event_index"]) == (mint_of(0), pool_of(0), WSOL_MINT, 1_000, 3, 0)
    assert "event_source" not in r  # legacy rows carry no event_source
    # event_v on the same block gives the same single row, from the log
    ev = mig_rows(event_v(migrate_block("logs")))
    assert len(ev) == 1 and ev[0]["event_source"] == "log"


def test_f2_truncated_logs_event_only_in_inner_instructions_legacy_zero_event_v_one():
    blk = migrate_block("truncated_inner")
    assert blk["transactions"][-1]["meta"]["logMessages"][-1] == "Log truncated"
    assert not any("Program data" in x for x in blk["transactions"][-1]["meta"]["logMessages"])
    leg, ev = legacy(blk), event_v(blk)
    assert mig_rows(leg) == [] and leg["migrations"] == []  # THE GAP: the follower's call sees nothing
    assert "bad_disc" not in leg and "cpi_only_disc" not in leg  # legacy counts nothing: no alarm possible
    rows = mig_rows(ev)
    assert len(rows) == 1 and rows[0]["event_source"] == "inner_event"
    assert ev["cpi_only_disc"] == {MIGRATE_DISC.hex(): 1} and ev["cpi_disc"] == {MIGRATE_DISC.hex(): 1} and ev["bad_disc"] == {}


def test_f3_cpi_only_with_logs_intact_legacy_zero_event_v_one():
    blk = migrate_block("cpi_only_logs_intact")
    assert not any("Program data" in x for x in blk["transactions"][-1]["meta"]["logMessages"]) and blk["transactions"][-1]["meta"]["logMessages"][-1] != "Log truncated"
    leg, ev = legacy(blk), event_v(blk)
    assert leg["migrations"] == []
    rows = mig_rows(ev)
    assert len(rows) == 1 and rows[0]["event_source"] == "inner_event"
    assert ev["cpi_only_disc"] == {MIGRATE_DISC.hex(): 1}


def test_f4_layout_variants_what_decode_migration_event_returns():
    base = migration_blob(0, HOUR0 - 90)
    assert len(base) == 200 and decode_migration_event(base) is not None
    # (a) a longer event (extra trailing field): the decoder reads fixed offsets and accepts it -> an extension alone does not drop rows
    longer = migration_blob(0, HOUR0 - 90, tail=bytes(40))
    d_long = decode_migration_event(longer)
    assert d_long is not None and d_long["pool"] == pool_of(0) and d_long["mint"] == mint_of(0)
    # (b) an extra 8-byte field inserted before the timestamp: ts offset moved to 136:144; the decoder reads 128:136 (the zero field) -> outside the sanity window -> None
    moved = migration_blob(0, HOUR0 - 90, extra_field_before_ts=u64(0), tail=bytes(8))
    assert decode_migration_event(moved) is None
    # (c) the same moved layout in the logs: no row in either mode and NO counter in either mode (event_v counts a rejected migrate blob as bad_disc only on the CPI path)
    blk = block(1_000, [tx("sigV", short_logs(moved))])
    leg, ev = legacy(blk), event_v(blk)
    assert leg["migrations"] == [] and "bad_disc" not in leg
    assert ev["migrations"] == [] and ev["bad_disc"] == {}
    # (d) the moved layout by CPI only: also no row in either mode (the fallback decodes with the same function) but event_v counts it
    blk2 = block(1_000, [tx("sigV2", truncated_logs(), [inner_cpi(moved)])])
    assert legacy(blk2)["migrations"] == [] and event_v(blk2)["migrations"] == [] and event_v(blk2)["bad_disc"] == {MIGRATE_DISC.hex(): 1}
    # (e) the longer event by CPI only is recovered by event_v and not by legacy
    blk3 = block(1_000, [tx("sigV3", truncated_logs(), [inner_cpi(longer)])])
    assert legacy(blk3)["migrations"] == [] and len(mig_rows(event_v(blk3))) == 1


# ------------------------------------------------------------------------------------------------------------------------------ F5: follower -> files -> TipReader -> TipAdapter -> Engine
class SetClock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


class ScriptRpc:
    def __init__(self, blocks: dict[int, dict]) -> None:
        self.blocks = blocks

    def get_tip(self) -> int:
        return max(self.blocks)

    def fetch(self, slot: int):
        b = self.blocks.get(slot)
        if b is None:
            return None, -32007  # skipped slot
        return json.loads(json.dumps(b)), None


def swap_trade(i: int, slot: int, k: int, block_time: int, v: int = 17_600_000_000) -> dict:
    """A PumpSwap trade row in the walker schema (what Engine.ingest_trade reads). Hand-written, not decoded from logs: this file does not exercise trade decoding."""
    return {"venue": "pumpswap", "mint": mint_of(i), "trader": f"t{k % 5}", "side": "buy" if k % 3 else "sell", "sol_lamports": 10**9 + k, "token_raw": 10**6,
            "quote_reserve": 85 * 10**9, "base_reserve": 206 * 10**12, "pool": pool_of(i), "slot": slot, "signature": f"p{i}x{slot}x{k}", "event_index": 0, "tx_index": 0,
            "block_time": block_time, "virtual_quote_reserves": v}


def run_follower(tmp_path: Path, blocks: dict[int, dict], inject_trades: dict[int, list[dict]], monkeypatch) -> tuple[Path, Path]:
    """Drive the REAL TipFollower (_commit/_emit, legacy rows_from_block call) over scripted blocks; PumpSwap trades are added to the decoded result of their slot by a wrapper
    around the same rows_from_block call, so they travel the same writer. Returns (tip dir, state dir)."""
    real = ftf.rows_from_block

    def wrapped(blk, cache, feed, **kw):
        assert kw == {}, "the follower must call rows_from_block in legacy mode (no event_v)"
        res = real(blk, cache, feed)
        res["trades"] = list(res["trades"]) + [dict(r) for r in inject_trades.get(blk["slot"], [])]
        return res

    monkeypatch.setattr(ftf, "rows_from_block", wrapped)
    rpc = ScriptRpc(blocks)
    f = ftf.TipFollower(out_dir=tmp_path / "tip", creates_dir=tmp_path / "creates", state_dir=tmp_path / "state", fetch=rpc.fetch, get_tip=rpc.get_tip,
                        clock_ms=SetClock((HOUR0 + 30) * 1000), sleep=lambda s: None, log=lambda m: None, max_retries=1)
    f.last_done = min(blocks) - 1
    f._checkpoint(None)
    for _ in range(50):
        if f.last_done >= max(blocks):
            break
        f.step()
    assert f.last_done == max(blocks)
    f.writer.close()
    f.cwriter.close()
    return tmp_path / "tip", tmp_path / "state"


def engine_from_tip(tip: Path, state: Path) -> tuple[lv.Engine, dict]:
    reader = tr.TipReader(tip, state, start_ms=(HOUR0 + 60) * 1000, catch_up_hours=0)
    items = reader.poll()
    counts: dict[str, int] = {}
    adapter = hg.TipAdapter()
    pda = {mint_of(i): pool_of(i) for i in range(3)}
    eng = lv.Engine(universe_min_mbt=0, pda_fn=lambda m: pda.get(m, "P_" + m), hash_fn=lambda s: zlib.crc32(str(s).encode()), score_fn=lambda b, X: X[:, 0])
    for kind, row in items:
        counts[kind] = counts.get(kind, 0) + 1
        if kind in ("migration", "create", "trade"):
            fa.apply(eng, adapter.feed({"type": "tip", "tip_kind": kind, "w": row}))
    assert reader.problem is None and reader.caught_up
    return eng, counts


def f5_blocks(variant: str) -> tuple[dict[int, dict], dict[int, list[dict]]]:
    """Slot 100: the buy tx that completes the curve (CompleteEvent in a SHORT log, so the `complete` row always survives). Slot 101: the migrate tx.
    Slots 102..104: PumpSwap prints of the pool at V0 = 17.6e9 (the member band)."""
    bt = HOUR0 - 100
    blocks = {
        100: block(100, [tx("sigC0", [f"Program {PUMP_BONDING_PROGRAM} invoke [1]", "Program log: Instruction: Buy", data_line(complete_blob(0, bt + 100)), "Program success"])], bt + 100),
        101: migrate_block(variant, 0, slot=101, tx_pos=0),
    }
    blocks[101]["blockTime"] = bt + 101
    trades: dict[int, list[dict]] = {}
    for n, slot in enumerate((102, 103, 104)):
        blocks[slot] = block(slot, [], bt + slot)
        trades[slot] = [swap_trade(0, slot, n, bt + slot)]
    return blocks, trades


@pytest.mark.parametrize("variant,migration_rows", [("logs", 1), ("truncated_inner", 0), ("cpi_only_logs_intact", 0)])
def test_f5_end_to_end_follower_to_engine_member(tmp_path, monkeypatch, variant, migration_rows):
    blocks, trades = f5_blocks(variant)
    tip, state = run_follower(tmp_path, blocks, trades, monkeypatch)
    # the follower's own file: migrations-<hour>.jsonl holds the complete row, and the migration row only for the logs variant
    lines = [json.loads(x) for x in (tip / f"migrations-{HOUR_KEY}.jsonl").read_text().splitlines()]
    assert sorted(r["type"] for r in lines) == sorted(["complete"] + ["migration"] * migration_rows)
    assert all(r["source"] == "tip" for r in lines)
    eng, counts = engine_from_tip(tip, state)
    assert counts["migration"] == 1 + migration_rows and counts["trade"] == 3  # the "complete" row also arrives as a tip "migration" item
    m = eng.member(mint_of(0))
    st = eng.mints[mint_of(0)]
    assert st.mslot == 100 and st.mbt is not None  # the complete row is there in every variant
    if migration_rows == 0:
        assert m is None and st.has_mig is False and st.mev_pool is None and st.mev_quote is None  # the mint is never a member, whatever it trades
    else:
        assert m is not None and (m.pool, m.s0, m.v0, m.mslot) == (pool_of(0), 102, 17_600_000_000, 100) and st.mev_quote == WSOL_MINT


def test_f5_member_fields_equal_when_the_migration_row_comes_from_the_cpi_path(tmp_path, monkeypatch):
    """The follower cannot produce the CPI row (that is the gap). Take the event_v row for the F2 tx and append it to the log-less tip file by hand: the engine then
    returns a Member with the same V0 and s0 as the logs variant. The row is written with the follower's own stamp (t_recv_ms, source)."""
    base, trades = f5_blocks("logs")
    tip_a, state_a = run_follower(tmp_path / "a", base, trades, monkeypatch)
    eng_a, _ = engine_from_tip(tip_a, state_a)
    ma = eng_a.member(mint_of(0))
    assert ma is not None
    blocks, trades = f5_blocks("truncated_inner")
    tip_b, state_b = run_follower(tmp_path / "b", blocks, trades, monkeypatch)
    cpi = mig_rows(event_v(blocks[101]))
    assert len(cpi) == 1
    path = tip_b / f"migrations-{HOUR_KEY}.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    for r in cpi:
        r["t_recv_ms"], r["source"] = (HOUR0 + 30) * 1000, ftf.TIP_SOURCE
    rows = sorted(rows + cpi, key=lambda r: (r["slot"], r.get("tx_index", 0), r.get("event_index", 0)))
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
    eng_b, _ = engine_from_tip(tip_b, state_b)
    mb = eng_b.member(mint_of(0))
    assert mb is not None and (mb.pool, mb.mslot, mb.mbt, mb.s0, mb.v0) == (ma.pool, ma.mslot, ma.mbt, ma.s0, ma.v0)


# ------------------------------------------------------------------------------------------------------------------------------ F6: key equality and decision md5
def test_f6_cpi_path_row_equals_log_path_row_on_every_key_the_engine_reads():
    by_log = mig_rows(event_v(migrate_block("logs")))
    for variant in ("truncated_inner", "cpi_only_logs_intact"):
        by_cpi = mig_rows(event_v(migrate_block(variant)))
        assert len(by_log) == len(by_cpi) == 1
        for k in MIG_KEYS:
            assert by_cpi[0][k] == by_log[0][k], (variant, k)
        extra = set(by_cpi[0]) ^ set(by_log[0])
        assert extra <= {"event_source"}  # the only key that differs; the engine does not read it
        assert by_cpi[0]["event_source"] == "inner_event" and by_log[0]["event_source"] == "log"
        assert by_cpi[0]["init_boost"] == by_log[0]["init_boost"]


def slot_at(bt: int) -> int:
    """A monotone slot clock for the 3-mint replay (2 slots per second), so block_time and slot agree and sd(T) is consistent."""
    return 1_000 + 2 * (bt - (HOUR0 - 4_000))


def three_mint_replay(variant: str, use_event_v: bool) -> tuple[dict, list[tuple], str, int]:
    """3 mints graduating ~50 minutes before the first mark T = HOUR0. Per mint i: a `complete` row (short log) and the migrate tx built as `variant`, then PumpSwap prints
    every 30 s at V0 in band until T + 5 min. Lifecycle rows come from rows_from_block (legacy = the follower's call, or event_v); trades are hand-written walker rows;
    all go straight into an Engine (the follower's t_recv_ms/source stamps are not read by the engine). Returns (counts, member tuples, md5 of the decision rows at T,
    number of decision rows)."""
    pda = {mint_of(i): pool_of(i) for i in range(3)}
    eng = lv.Engine(universe_min_mbt=0, pda_fn=lambda m: pda.get(m, "P_" + m), hash_fn=lambda s: zlib.crc32(str(s).encode()), score_fn=lambda b, X: X[:, 0] if len(X) else X)
    stream: list[tuple[int, int, str, dict]] = []  # (slot, order, kind, row)
    counts = {"complete": 0, "migration": 0}
    decode = event_v if use_event_v else legacy
    for i in range(3):
        bt_g = HOUR0 - 3_000 + 30 * i
        cb = block(slot_at(bt_g), [tx(f"sigC{i}", [f"Program {PUMP_BONDING_PROGRAM} invoke [1]", data_line(complete_blob(i, bt_g)), "Program success"])], bt_g)
        mb = migrate_block(variant, i, slot=slot_at(bt_g) + 2, tx_pos=0)
        mb["blockTime"] = bt_g + 1
        for blk in (cb, mb):
            for r in decode(blk)["migrations"]:
                counts[r["type"]] += 1
                stream.append((r["slot"], 0, "migration", r))
        for k, bt in enumerate(range(bt_g + 3, HOUR0 + 300, 30)):
            stream.append((slot_at(bt), 1, "trade", swap_trade(i, slot_at(bt), k, bt)))
    stream.sort(key=lambda e: (e[0], e[1]))
    for _slot, _o, kind, row in stream:
        {"migration": eng.ingest_migration, "trade": eng.ingest_trade}[kind](row)
    members = [(m.mint, m.pool, m.mslot, m.mbt, m.s0, m.v0) for m in eng.members()]
    res = eng.decide(HOUR0)
    md5 = lv.rows_md5(res.mints, res.T, res.sdv, res.X, res.score)
    return counts, members, md5, len(res.mints)


def test_f6_decision_rows_md5_equal_log_path_vs_cpi_path_via_event_v():
    c1, m1, d1, n1 = three_mint_replay("logs", use_event_v=False)  # F1 rows, as the follower decodes them
    assert c1 == {"complete": 3, "migration": 3} and len(m1) == 3
    for variant in ("truncated_inner", "cpi_only_logs_intact"):
        c2, m2, d2, n2 = three_mint_replay(variant, use_event_v=True)  # F2/F3 rows, as event_v decodes them
        assert c2 == c1 and m2 == m1
        assert d2 == d1 and n2 == n1  # n is the number of decision rows (non-zero is asserted below)
        # the same tx under the follower's legacy call: no migration rows, no members, a different (empty) decision set
        c3, m3, d3, n3 = three_mint_replay(variant, use_event_v=False)
        assert c3 == {"complete": 3, "migration": 0} and m3 == [] and n3 == 0
        assert d3 != d1 or n1 == 0
    assert n1 > 0, "the synthetic replay must produce decision rows, or the md5 equality above is vacuous"
    assert hashlib.md5(json.dumps(m1).encode()).hexdigest() == hashlib.md5(json.dumps(three_mint_replay("cpi_only_logs_intact", True)[1]).encode()).hexdigest()
