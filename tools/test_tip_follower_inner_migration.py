"""Tests for the follower's inner-instruction migration rows (QP-RULING-1107-tip-migration-crossbook s3, form (i-a)).
SYNTHETIC blocks and tmp dirs only: no tip tape, derived copy, network or SSH.

  /data/mal/venv/bin/python -m pytest tools/test_tip_follower_inner_migration.py -q

F7 (H5 path) runs in a subprocess against a frozen checkout of commit afa4971d (archive of tools/ and observe/ into a tmp dir): that commit's h5_shadow,
h5_shadow_tip, arena_tip_adapter and test tape builders. It fails (never skips) when the object is absent, unless MAL_SKIP_FROZEN=1.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import types
from pathlib import Path

import pytest
from solders.pubkey import Pubkey

from tools import fast_tip_follower as ftf
from tools.pumpswap_tx import canonical_pool
from tools.test_tip_migration_gap import (HOUR0, ScriptRpc, SetClock, block, complete_blob, data_line, inner_cpi, key, mig_rows, migration_blob, pk, short_logs,
                                          swap_trade, truncated_logs, tx)

REPO = Path(__file__).resolve().parent.parent
BASE_COMMIT = "c658b51f"  # main before this change: the follower without the inner-migration pass
FROZEN = "afa4971d"
DECODERS = ("observe/trade_decode.py", "observe/trade_store.py", "tools/pump_history_backfill.py")


def git(*args: str, **kw):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, stdin=subprocess.DEVNULL, timeout=120, **kw)


def canon_pool(i: int) -> str:
    return str(canonical_pool(Pubkey.from_bytes(pk(10 + i))))


def cblob(i: int, ts: int = HOUR0 - 90) -> bytes:
    """A migration event whose pool is the canonical PumpSwap PDA of its mint."""
    b = migration_blob(i, ts)
    return b[:136] + bytes(Pubkey.from_string(canon_pool(i))) + b[168:]


def bad_pool_blob(i: int) -> bytes:
    """Same event with the pool field shifted by one byte: a wrong (non-canonical) pool that decode_migration_event still accepts."""
    b = cblob(i)
    return b[:136] + b[137:168] + b"\x07" + b[168:]


def plain_tx(sig: str) -> dict:
    return tx(sig, ["Program x invoke [1]", "Program x success"])


def script_blocks() -> dict[int, dict]:
    bt = HOUR0 - 100
    b: dict[int, dict] = {}

    def put(slot, txs):
        b[slot] = block(slot, txs, bt + slot - 90)

    put(100, [plain_tx("sigP0"), tx("sigM0", short_logs(cblob(0)))])  # log path
    put(101, [plain_tx("sigP1"), tx("sigM1", truncated_logs(), [inner_cpi(cblob(1))])])  # logs cut, event in inner instructions only
    put(102, [tx("sigM2", short_logs(None), [inner_cpi(cblob(2))])])  # self-CPI only, logs whole
    put(103, [plain_tx("sigP3"), plain_tx("sigP3b"), tx("sigM3", short_logs(cblob(3)), [inner_cpi(cblob(3))])])  # log AND inner: one row
    put(104, [tx("sigM4", truncated_logs(), [inner_cpi(bad_pool_blob(4))])])  # shifted pool: dropped by the guard
    put(105, [tx("sigC5", ["Program p invoke [1]", data_line(complete_blob(5, bt + 15)), "Program p success"])])
    put(107, [plain_tx("sigP7")])  # 106 is a skipped slot
    return b


def trades_for() -> dict[int, list[dict]]:
    return {s: [swap_trade(i, s, s, HOUR0 - 100 + s - 90)] for i, s in ((0, 100), (1, 101), (2, 102))}


def run_follower(mod, root: Path, blocks: dict[int, dict]):
    real = mod.rows_from_block
    inject = trades_for()

    def wrapped(blk, cache, feed, **kw):
        res = real(blk, cache, feed, **kw)
        if not kw:  # the legacy pass carries the hand-written PumpSwap trades; an event_v pass must not
            res["trades"] = list(res["trades"]) + [dict(r) for r in inject.get(blk["slot"], [])]
        return res

    mod.rows_from_block = wrapped
    try:
        rpc = ScriptRpc(blocks)
        f = mod.TipFollower(out_dir=root / "tip", creates_dir=root / "creates", state_dir=root / "state", fetch=rpc.fetch, get_tip=rpc.get_tip,
                            clock_ms=SetClock((HOUR0 + 30) * 1000), sleep=lambda s: None, log=lambda m: None, max_retries=1)
        f.last_done = min(blocks) - 1
        f._checkpoint(None)
        for _ in range(60):
            if f.last_done >= max(blocks):
                break
            f.step()
        assert f.last_done == max(blocks)
        f.writer.close()
        f.cwriter.close()
    finally:
        mod.rows_from_block = real
    return f


def base_module():
    r = git("show", f"{BASE_COMMIT}:tools/fast_tip_follower.py")
    if r.returncode != 0:
        pytest.skip(f"{BASE_COMMIT} not in this clone")
    mod = types.ModuleType("fast_tip_follower_base_c658b51f")
    mod.__file__ = "fast_tip_follower_base.py"
    exec(compile(r.stdout.decode(), mod.__file__, "exec"), mod.__dict__)
    assert "inner_migration_rows" not in mod.__dict__
    return mod


def files(root: Path) -> dict[str, bytes]:
    out = {}
    for sub in ("tip", "creates"):
        d = root / sub
        if d.is_dir():
            out.update({f"{sub}/{p.name}": p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()})
    return out


def kind(name: str) -> str:
    return name.split("/", 1)[1].split("-2", 1)[0]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    po, pn = tmp_path_factory.mktemp("old"), tmp_path_factory.mktemp("new")
    old = run_follower(base_module(), po, script_blocks())
    new = run_follower(ftf, pn, script_blocks())
    return old, new, files(po), files(pn)


def test_every_non_migration_stream_is_byte_identical(runs):
    _o, _n, fo, fn = runs
    assert set(fo) == set(fn)
    assert {"trades", "migrations", "skipped-slots", "gaps.jsonl"} <= {kind(k) for k in fo}
    checked = 0
    for name in fo:
        if kind(name) != "migrations":
            assert fn[name] == fo[name], name
            assert hashlib.md5(fn[name]).hexdigest() == hashlib.md5(fo[name]).hexdigest()
            checked += 1
    assert checked >= 3


def test_only_inner_event_migration_rows_are_added(runs):
    _o, _n, fo, fn = runs
    name = next(k for k in fo if kind(k) == "migrations")
    old_lines, new_lines = fo[name].splitlines(), fn[name].splitlines()
    assert [x for x in new_lines if b'"event_source":"inner_event"' not in x] == old_lines  # every old row present, byte-identical, same order
    added = [json.loads(x) for x in new_lines if b'"event_source":"inner_event"' in x]
    assert sorted(r["signature"] for r in added) == ["sigM1", "sigM2"]
    assert all(r["type"] == "migration" and r["source"] == "tip" for r in added)
    assert {r["pool"] for r in added} == {canon_pool(1), canon_pool(2)}
    assert len(new_lines) == len(old_lines) + 2


def test_double_count_guard_log_plus_inner_gives_one_row(runs):
    _o, _n, _fo, fn = runs
    name = next(k for k in fn if kind(k) == "migrations")
    rows = [json.loads(x) for x in fn[name].splitlines()]
    for sig in ("sigM0", "sigM3"):
        got = [r for r in rows if r["signature"] == sig]
        assert len(got) == 1 and "event_source" not in got[0], sig  # the log row, unchanged


def test_wrong_pool_row_dropped_counted_and_status_additive(runs):
    old, new, _fo, fn = runs
    assert new.migrations_inner_added == 2 and new.migrations_inner_pool_mismatch == 1 and new.migrations_inner_errors == 0
    assert not any(b"sigM4" in v for v in fn.values())
    so, sn = old.status(), new.status()
    assert set(sn) - set(so) == {"migrations_inner_added", "migrations_inner_errors", "migrations_inner_pool_mismatch"}
    assert set(so) <= set(sn)
    assert new.decoder_blobs == old.decoder_blobs == ftf.PINNED_DECODER_BLOBS
    assert len(new.pool_cache) == len(old.pool_cache)  # the second pass does not touch the follower's pool cache


def test_decoder_files_are_untouched_by_this_change():
    r = git("diff", "--name-only", BASE_COMMIT, "--", *DECODERS, text=True)
    if r.returncode != 0:
        pytest.skip("git cannot diff here")
    assert r.stdout.strip() == ""


def test_inner_migration_rows_is_pure_keeps_tx_index_and_guards_pool():
    blks = script_blocks()
    blk = blks[101]
    blk["slot"] = 101
    legacy = ftf.rows_from_block(copy.deepcopy(blk), {}, ftf.FEED)
    before = json.dumps(blk, sort_keys=True)
    stats: dict = {}
    rows = ftf.inner_migration_rows(blk, legacy, stats)
    assert json.dumps(blk, sort_keys=True) == before and stats == {}  # input block not mutated
    assert len(rows) == 1 and rows[0]["tx_index"] == 1 and rows[0]["event_source"] == "inner_event"
    bad = blks[104]
    bad["slot"] = 104
    legacy_bad = ftf.rows_from_block(copy.deepcopy(bad), {}, ftf.FEED)
    st: dict = {}
    assert ftf.inner_migration_rows(bad, legacy_bad, st) == [] and st == {"pool_mismatch": 1}
    # the guard is the injected comparison: accepting every pool keeps the row
    assert len(ftf.inner_migration_rows(bad, legacy_bad, None, lambda m: mig_rows(ftf.rows_from_block(copy.deepcopy(bad), {}, ftf.FEED, event_v=True))[0]["pool"])) == 1


# ------------------------------------------------------------------------------------------------------------------------------ F7
DRIVER = r'''
import collections, json, sys, hashlib
from tools import h5_shadow as h5, h5_shadow_tip as tip, h5_shadow_tip_equiv as eq, arena_tip_adapter as ta
from tools.test_h5_shadow import make_engine, V0
from tools.test_h5_shadow_fresh_forward import pool_tape
inp = json.load(sys.stdin)
pool, mint = inp["pool"], inp["mint"]
t = pool_tape(pool, 1000, 1_800_000_000, filler=True)
meta = {pool: {"s0": t.slot0, "v": V0, "mint": mint}}
rows = eq.to_tip_rows(eq.prepare_rows(t.rows), meta)
prints = [r for r in rows if r["type"] != "pool_create"]
first = prints[0]

def run(create):
    eng, out = make_engine()
    c = collections.Counter()
    for r in ([create] if create else []) + prints:
        tip.apply_row(eng, r, c)
    eng.close_all("shutdown")
    trig = [eq._canon(eq.strip_stamps(r)) for r in out if r.get("type") == "trigger"]
    return {"triggers": len(trig), "md5": hashlib.md5("\n".join(trig).encode()).hexdigest(), "rejected_unannounced": int(eng.counters.get("rejected_unannounced", 0))}

res = {"none": run(None)}
for name, w in inp["rows"].items():
    res[name] = run(ta.pool_create_row(dict(w, slot=first["slot"] - 1, t_recv_ms=first["t_recv_ms"])))
print(json.dumps(res))
'''


def frozen_tree(dest: Path) -> Path:
    r = git("archive", FROZEN, "tools", "observe")
    if r.returncode != 0:
        if os.environ.get("MAL_SKIP_FROZEN") == "1":
            pytest.skip(f"{FROZEN} not in this clone")
        raise AssertionError(f"object {FROZEN} is not in this clone: git fetch it")
    tar = dest / "frozen.tar"
    tar.write_bytes(r.stdout)
    with tarfile.open(tar) as t:
        t.extractall(dest / "tree", filter="data")
    return dest / "tree"


def test_f7_h5_tip_path_inner_pool_create_equals_log_pool_create(tmp_path):
    tree = frozen_tree(tmp_path)
    blob = git("rev-parse", f"{FROZEN}:tools/h5_shadow_tip.py", text=True).stdout.strip()
    assert blob == "05c5489a786dac4eb0696a74279a0f789ddb1deb"  # the frozen tip wrapper the book pins
    # migration rows as the NEW follower writes them: the F1 log-path row and the F2 inner-path row, same event
    log_row = mig_rows(ftf.rows_from_block(block(1_000, [tx("sigM", short_logs(cblob(0)))]), {}, ftf.FEED))[0]
    blk = block(1_000, [tx("sigM", truncated_logs(), [inner_cpi(cblob(0))])])
    legacy = ftf.rows_from_block(copy.deepcopy(blk), {}, ftf.FEED)
    assert legacy["migrations"] == []
    inner_row = ftf.inner_migration_rows(blk, legacy)[0]
    assert inner_row["event_source"] == "inner_event" and log_row["pool"] == inner_row["pool"] == canon_pool(0)
    payload = {"pool": canon_pool(0), "mint": key(10), "rows": {"log": {**log_row, "t_recv_ms": 1}, "inner": {**inner_row, "t_recv_ms": 1}}}
    script = tmp_path / "driver.py"
    script.write_text("import sys\nsys.path.insert(0, %r)\n" % str(tree) + DRIVER)
    env = {"PATH": os.environ.get("PATH", "")}
    r = subprocess.run([sys.executable, str(script)], input=json.dumps(payload), capture_output=True, text=True, cwd=str(tree), env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["none"]["rejected_unannounced"] > 0 and res["none"]["triggers"] == 0  # prints without pool_create are refused
    assert res["log"]["triggers"] >= 1 and res["log"]["rejected_unannounced"] == 0
    assert res["inner"]["triggers"] == res["log"]["triggers"] and res["inner"]["md5"] == res["log"]["md5"]


def _v2(logs: list[str]) -> list[str]:
    return [x.replace("Instruction: Migrate", "Instruction: MigrateV2") for x in logs]


def test_2235_shape_migrate_v2_truncated_logs_inner_event_gives_one_row_and_whole_logs_no_duplicate(tmp_path_factory):
    """Shape seen in job #2235: logs end in "Log truncated" with no migration Program data line; the event sits in the emit_cpi inner instruction.
    The same tx with whole logs (event in Program data AND in the CPI) gives one row from the log path."""
    bt = HOUR0 - 100
    blocks = {
        200: block(200, [plain_tx("sigQ0"), tx("sigV2T", _v2(truncated_logs()), [inner_cpi(cblob(6))])], bt + 110),
        201: block(201, [tx("sigV2W", _v2(short_logs(cblob(7))), [inner_cpi(cblob(7))])], bt + 111),
        202: block(202, [plain_tx("sigQ2")], bt + 112),
    }
    po, pn = tmp_path_factory.mktemp("o2235"), tmp_path_factory.mktemp("n2235")
    old = run_follower(base_module(), po, copy.deepcopy(blocks))
    new = run_follower(ftf, pn, copy.deepcopy(blocks))
    fo, fn = files(po), files(pn)
    assert set(fo) == set(fn)
    for name in fo:
        if kind(name) != "migrations":
            assert fn[name] == fo[name], name  # trades, creates, skipped, gaps, observe: byte-identical
    name = next(k for k in fn if kind(k) == "migrations")
    old_rows = [json.loads(x) for x in fo[name].splitlines()]
    new_rows = [json.loads(x) for x in fn[name].splitlines()]
    assert [r["signature"] for r in old_rows] == ["sigV2W"]  # the old follower misses the truncated tx
    t = [r for r in new_rows if r["signature"] == "sigV2T"]
    assert len(t) == 1 and t[0]["event_source"] == "inner_event" and t[0]["pool"] == canon_pool(6)
    w = [r for r in new_rows if r["signature"] == "sigV2W"]
    assert len(w) == 1 and "event_source" not in w[0]  # log path only, no duplicate from the CPI
    assert len(new_rows) == 2
    assert new.pool_cache == old.pool_cache  # the second pass leaves the follower's pool cache as it was
