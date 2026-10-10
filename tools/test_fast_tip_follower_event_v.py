"""Tip follower event-V stamper (--trade-event-v). Offline: repo fixtures only, no network, no host.

Covers the design's P1 and quant-proof's 10-10 ruling, (a) both gaps and (c) item 2:
  * flag off: the six output streams are byte-identical to the follower at main b17dc0b (golden md5s,
    plus a live run of that commit's follower when git can read it);
  * flag on: trades with EVENT_V_KEYS dropped, and every other stream, are byte-identical to flag off;
  * (d) the stamped keys equal rows_from_block(..., event_v=True) (+ resolve_unresolved) on the October
    fixtures that tools/test_walk2_event_v.py uses, and every PumpSwap Buy/Sell row has an int V;
  * (e) a row that came back through resolve_unresolved carries the stamp;
  * (f) a print without an event-V tail is counted and left without the keys (no chaining).
The fixtures are decoder fixtures (public getTransaction results); no price, return or P&L is derived.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import subprocess
import types
from pathlib import Path

import pytest

from observe.trade_decode import EVENT_V_KEYS, WSOL_MINT, decode_program_data, event_v_fields
from tools import fast_tip_follower as ftf
from tools.pump_history_backfill import resolve_unresolved, rows_from_block
from tools.walk2_decoder_replay import fixture_blocks

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tools" / "fixtures" / "walk2_event_v"
BASE_COMMIT = "b17dc0b"  # main when the stamper was added; its follower has no event-V code
START = 100
CLOCK0 = 1_790_318_933_000
SELL_DISC = "3e2f370aa503dc2a"

# md5 of each output stream of the b17dc0b follower on _feed() (computed by running that commit's
# tools/fast_tip_follower.py with this file's harness; the live check below re-runs it when git can).
GOLDEN_FLAG_OFF: dict[str, str] = {
    "creates/observe-2026-09-25.jsonl": "5c534c396e27045db88c8d2009344ece",
    "out/creates-2026-09-25T06.jsonl": "278d86068b9fa7cb7b26ed4ca3c191a4",
    "out/gaps.jsonl": "08c38343197d8119fc3c370f2aadd04a",
    "out/migrations-2026-09-25T06.jsonl": "14d6f73492c87ea551a5b89f89e6956c",
    "out/skipped-slots-2026-09-25T06.jsonl": "b4791b141b1fc0e02bbc2e4aa480bc26",
    "out/trades-2026-09-25T06.jsonl": "6256f5ea0666f1023549e153915265cd",
}


class Clock:
    def __init__(self, ms: int = CLOCK0) -> None:
        self.ms = ms

    def __call__(self) -> int:
        self.ms += 7
        return self.ms


def _lookup(pools):
    return {p: ("MINT" + p[:6], WSOL_MINT) for p in pools}


def _v_lookup(pools):
    return {p: 17_584_000_000 for p in pools}


def _feed() -> tuple[dict[int, object], int]:
    """Slot -> outcome. 101.. one fixture tx per block (the walk-2 event-V fixtures, then the September
    pump_structure_monitor ones test_walk2_event_v.py also uses, for bonding trades and migrations), then a
    skipped slot, a gap, and one block with every fixture tx in it (pools created earlier resolve from the
    cache there)."""
    blocks = fixture_blocks()
    script: dict[int, object] = {}
    slot = START
    for b in blocks:
        slot += 1
        blk = {k: v for k, v in b.items() if k != "_name"}
        blk["parentSlot"] = slot - 1
        script[slot] = blk
    last_block = slot
    script[slot + 1] = "skip"
    script[slot + 2] = "gap"
    allin = {
        "slot": slot + 3,
        "blockTime": blocks[0]["blockTime"],
        "parentSlot": last_block,
        "transactions": [b["transactions"][0] for b in blocks],
    }
    script[slot + 3] = allin
    return script, slot + 3


class FeedRpc:
    def __init__(self, script, tip, mod) -> None:
        self.script = script
        self.tip = tip
        self.mod = mod  # the follower module under test (its own TransientError class)

    def get_tip(self) -> int:
        return self.tip

    def fetch(self, slot):
        item = self.script[slot]
        if item == "skip":
            return None, -32007
        if item == "gap":
            raise self.mod.TransientError("boom")
        return copy.deepcopy(item), None


def _run(mod, tmp: Path, script=None, tip=None, **kw):
    if script is None:
        script, tip = _feed()
    rpc = FeedRpc(script, tip, mod)
    f = mod.TipFollower(
        out_dir=tmp / "out",
        creates_dir=tmp / "creates",
        state_dir=tmp / "state",
        fetch=rpc.fetch,
        get_tip=rpc.get_tip,
        lookup=kw.pop("lookup", _lookup),
        v_lookup=_v_lookup,
        clock_ms=Clock(),
        sleep=lambda s: None,
        log=lambda m: None,
        max_retries=1,
        **kw,
    )
    f.last_done = START if min(script) > START else min(script) - 1
    f._checkpoint(None)
    for _ in range(100):
        if f.last_done >= tip:
            break
        f.step()
    assert f.last_done == tip
    f.writer.close()
    f.cwriter.close()
    return f


def _streams(tmp: Path) -> dict[str, bytes]:
    out = {}
    for base in ("out", "creates"):
        for p in sorted((tmp / base).glob("*.jsonl")):
            out[f"{base}/{p.name}"] = p.read_bytes()
    return out


def _md5s(streams: dict[str, bytes]) -> dict[str, str]:
    return {k: hashlib.md5(v).hexdigest() for k, v in streams.items()}


def _kind(name: str) -> str:
    return name.split("/", 1)[1].split("-2", 1)[0]


def _drop_event_v(data: bytes) -> bytes:
    lines = []
    for line in data.splitlines():
        row = json.loads(line)
        lines.append(json.dumps({k: v for k, v in row.items() if k not in EVENT_V_KEYS}, separators=(",", ":")).encode())
    return b"".join(x + b"\n" for x in lines)


def _trades(streams: dict[str, bytes]) -> list[dict]:
    rows = []
    for name, data in streams.items():
        if _kind(name) == "trades":
            rows += [json.loads(x) for x in data.splitlines()]
    return rows


def _expected_event_v(script) -> dict[tuple, dict]:
    """(slot, signature, event_index) -> EVENT_V_KEYS of rows_from_block(event_v=True) + resolve_unresolved."""
    pools: dict = {}
    exp: dict[tuple, dict] = {}
    for slot in sorted(script):
        blk = script[slot]
        if not isinstance(blk, dict):
            continue
        blk = copy.deepcopy(blk)
        blk["slot"] = slot
        res = rows_from_block(blk, pools, ftf.FEED, event_v=True)
        ready, dropped = resolve_unresolved(res["unresolved"], pools, blk["blockTime"], _lookup)
        assert dropped == 0
        for r in res["trades"] + ready:
            exp[(slot, r["signature"], r["event_index"])] = {k: r[k] for k in EVENT_V_KEYS if k in r}
    return exp


def _base_module(tmp: Path):
    try:
        src = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{BASE_COMMIT}:tools/fast_tip_follower.py"],
            capture_output=True, check=True, timeout=30, stdin=subprocess.DEVNULL,
        ).stdout.decode()
    except (OSError, subprocess.SubprocessError):
        return None
    mod = types.ModuleType("fast_tip_follower_base")
    mod.__file__ = str(tmp / "fast_tip_follower_base.py")
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    assert "trade_event_v" not in src
    return mod


# ---------------------------------------------------------------- flag off: byte-identical to main


def test_flag_off_md5_equals_golden_base(tmp_path):
    _run(ftf, tmp_path)
    got = _md5s(_streams(tmp_path))
    assert {_kind(k) for k in got} >= {"trades", "creates", "migrations", "skipped-slots", "gaps.jsonl", "observe"}
    assert got == GOLDEN_FLAG_OFF


def test_flag_off_equals_base_commit_follower_live(tmp_path):
    base = _base_module(tmp_path)
    if base is None:
        pytest.skip(f"git cannot read {BASE_COMMIT} here; the golden md5 test still runs")
    _run(base, tmp_path / "base")
    _run(ftf, tmp_path / "new")
    assert _streams(tmp_path / "new") == _streams(tmp_path / "base")
    assert _md5s(_streams(tmp_path / "base")) == GOLDEN_FLAG_OFF


def test_flag_default_is_off(tmp_path):
    f = _run(ftf, tmp_path)
    assert f.trade_event_v is False and f.event_v_stamped == 0
    assert not any(set(r) & set(EVENT_V_KEYS) for r in _trades(_streams(tmp_path)))


# ---------------------------------------------------------------- flag on


def test_flag_on_only_adds_event_v_keys(tmp_path):
    _run(ftf, tmp_path / "off")
    f = _run(ftf, tmp_path / "on", trade_event_v=True)
    off, on = _streams(tmp_path / "off"), _streams(tmp_path / "on")
    assert set(off) == set(on)
    for name in off:
        if _kind(name) == "trades":
            assert on[name] != off[name]
            assert _drop_event_v(on[name]) == off[name], name
        else:
            assert on[name] == off[name], name
    assert f.event_v_stamped > 0 and f.event_v_mismatch == 0 and f.event_v_errors == 0


def test_d_stamped_keys_equal_rows_from_block_event_v(tmp_path):
    script, tip = _feed()
    f = _run(ftf, tmp_path, trade_event_v=True)
    rows = _trades(_streams(tmp_path))
    exp = _expected_event_v(script)
    got = {(r["slot"], r["signature"], r["event_index"]): {k: r[k] for k in EVENT_V_KEYS if k in r} for r in rows}
    assert len(got) == len(rows) and set(got) == set(exp)
    assert got == exp
    swaps = [r for r in rows if r.get("venue") == "pumpswap"]
    assert swaps and all(type(r.get("virtual_quote_reserves")) is int for r in swaps)
    assert f.event_v_missing_pumpswap == 0 and f.event_v_mismatch == 0
    assert f.event_v_stamped == sum(1 for v in got.values() if v)
    assert f.event_v_missing == sum(1 for v in got.values() if not v)
    # the singular cached key is untouched by the stamper
    assert all(r["virtual_quote_reserve"] == 17_584_000_000 for r in swaps)


def test_e_unresolved_then_resolved_row_carries_the_stamp(tmp_path):
    script, tip = _feed()
    asked: list[str] = []

    def lookup(pools):
        asked.extend(pools)
        return _lookup(pools)

    _run(ftf, tmp_path, trade_event_v=True, lookup=lookup)
    rows = _trades(_streams(tmp_path))
    # stored_trade drops mint_source on PumpSwap rows; the stub lookup's mints mark the resolved ones
    resolved = [r for r in rows if r.get("venue") == "pumpswap" and str(r.get("mint")).startswith("MINT")]
    assert asked and resolved, "the fixtures must exercise resolve_unresolved"
    assert {r["pool"] for r in resolved} <= set(asked)
    exp = _expected_event_v(script)
    for r in resolved:
        assert type(r["virtual_quote_reserves"]) is int
        assert {k: r[k] for k in EVENT_V_KEYS if k in r} == exp[(r["slot"], r["signature"], r["event_index"])]


def _sell_doc_tx(sig: str, truncate_to: int | None) -> dict:
    doc = json.loads((FIX / "sell_v2_kept.json").read_text(encoding="utf-8"))
    meta = copy.deepcopy(doc["meta"])
    if truncate_to is not None:
        logs = []
        for line in meta["logMessages"]:
            if "Program data: " in line:
                raw = base64.b64decode(line.split("Program data: ", 1)[1].strip())
                if raw[:8].hex() == SELL_DISC:
                    line = "Program data: " + base64.b64encode(raw[:truncate_to]).decode()
            logs.append(line)
        meta["logMessages"] = logs
    return {
        "transaction": {"signatures": [sig], "message": {"accountKeys": doc["accountKeys"], "instructions": []}},
        "meta": meta,
    }, doc


def _short_tail_len(doc: dict) -> int:
    """Longest sell blob the legacy decoder still reads but that has no event-V tail."""
    raw = next(
        base64.b64decode(l.split("Program data: ", 1)[1].strip())
        for l in doc["meta"]["logMessages"]
        if "Program data: " in l and base64.b64decode(l.split("Program data: ", 1)[1].strip())[:8].hex() == SELL_DISC
    )
    for n in range(len(raw) - 1, 0, -1):
        if event_v_fields(raw[:n]) == {} and decode_program_data(raw[:n]) is not None:
            return n
    raise AssertionError("no legacy-readable sell without a tail")


def test_f_missing_tail_is_counted_never_chained(tmp_path):
    _, doc = _sell_doc_tx("x", None)
    n = _short_tail_len(doc)
    full, _ = _sell_doc_tx("SigFull" + "1" * 80, None)
    short, _ = _sell_doc_tx("SigShort" + "1" * 80, n)
    blk = {"slot": START + 1, "blockTime": doc["blockTime"], "parentSlot": START, "transactions": [full, short]}
    f = _run(ftf, tmp_path, script={START + 1: blk}, tip=START + 1, trade_event_v=True)
    rows = {r["signature"][:8]: r for r in _trades(_streams(tmp_path))}
    assert rows["SigFull1"]["pool"] == rows["SigShort"]["pool"]  # same pool, the earlier print has V
    assert type(rows["SigFull1"]["virtual_quote_reserves"]) is int
    assert not set(rows["SigShort"]) & set(EVENT_V_KEYS)
    assert (f.event_v_stamped, f.event_v_missing, f.event_v_missing_pumpswap, f.event_v_mismatch) == (1, 1, 1, 0)
    st = f.status()
    assert st["event_v_missing"] == 1 and st["event_v_missing_pumpswap"] == 1


def test_mismatch_is_counted_and_not_stamped(tmp_path, monkeypatch):
    real = ftf.records_from_logs

    def skewed(*a, **kw):
        recs = real(*a, **kw)
        for r in recs:
            if isinstance(r.get("sol_lamports"), int):
                r["sol_lamports"] += 1
        return recs

    monkeypatch.setattr(ftf, "records_from_logs", skewed)
    f = _run(ftf, tmp_path / "on", trade_event_v=True)
    _run(ftf, tmp_path / "off")
    on, off = _streams(tmp_path / "on"), _streams(tmp_path / "off")
    assert on == off  # nothing stamped when the re-decode disagrees with the row
    assert f.event_v_mismatch == len(_trades(on)) and f.event_v_stamped == 0


def test_decode_error_counted_feed_continues(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise ValueError("bad blob")

    monkeypatch.setattr(ftf, "records_from_logs", boom)
    f = _run(ftf, tmp_path / "on", trade_event_v=True)
    _run(ftf, tmp_path / "off")
    assert _streams(tmp_path / "on") == _streams(tmp_path / "off")
    assert f.event_v_errors > 0 and f.event_v_stamped == 0


# ---------------------------------------------------------------- status, flag plumbing, pins


def test_status_has_counters_and_decoder_blobs(tmp_path):
    f = _run(ftf, tmp_path, trade_event_v=True)
    f.heartbeat(force=True)
    st = json.loads((tmp_path / "state" / "status.json").read_text())
    for k in ("event_v_stamped", "event_v_missing", "event_v_missing_pumpswap", "event_v_mismatch", "event_v_errors"):
        assert isinstance(st[k], int)
    assert st["trade_event_v"] is True
    assert set(st["decoder_blobs"]) == set(ftf.PINNED_DECODER_BLOBS)
    assert st["decoder_blobs_pinned"] is (st["decoder_blobs"] == ftf.PINNED_DECODER_BLOBS)


def test_git_blob_sha_matches_git_hash_object(tmp_path):
    p = tmp_path / "x.py"
    p.write_bytes(b"print('hi')\n")
    try:
        want = subprocess.run(["git", "hash-object", str(p)], capture_output=True, check=True, timeout=30,
                              stdin=subprocess.DEVNULL).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    assert ftf.git_blob_sha(p.read_bytes()) == want


def test_pins_are_job_433_blobs():
    assert ftf.PINNED_DECODER_BLOBS == {
        "observe/trade_decode.py": "238942a6b3c5425389eddfde4d11268c300acbec",
        "observe/trade_store.py": "ea4e11eddf9f034e3bc7318ce8743337d753f350",
        "tools/pump_history_backfill.py": "9a8bebb32adcf86de060b55f5a08110d11c0a550",
    }


def test_check_decoder_pins_cli(capsys, monkeypatch):
    rc = ftf.main(["--check-decoder-pins"])
    out = json.loads(capsys.readouterr().out)
    assert rc == (0 if out["pinned"] else 1)
    monkeypatch.setattr(ftf, "PINNED_DECODER_BLOBS", {"observe/trade_decode.py": "0" * 40})
    assert ftf.main(["--check-decoder-pins"]) == 1


def test_env_flag():
    assert ftf.env_flag({}) is False
    assert ftf.env_flag({ftf.TRADE_EVENT_V_ENV: "0"}) is False
    for v in ("1", "true", "YES", "on"):
        assert ftf.env_flag({ftf.TRADE_EVENT_V_ENV: v}) is True
