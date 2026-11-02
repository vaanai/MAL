"""r4bt_decode_parity: synthetic recorded-event fixtures only (observe/fixtures/*.b64 and hand-built CreatePool / Create / Complete blobs). No RPC, no network, no tape, no
decision log. The failure strings must carry names and counts, never a value of the sample."""
from __future__ import annotations

import base64
import collections
from pathlib import Path

import pytest

from observe import trade_decode as td
from tools import a3_redeploy_check as ARC
from tools import arena_feed as AF
from tools import r4bt_a3 as A
from tools import r4bt_decode_check as D
from tools import r4bt_decode_parity as P

FIX = Path(__file__).resolve().parent.parent / "observe" / "fixtures"
WSOL = "So11111111111111111111111111111111111111112"
POOL = td.b58encode(bytes([7]) * 32)
MINT = td.b58encode(bytes([9]) * 32)
CURVE = td.b58encode(bytes([5]) * 32)
USER = td.b58encode(bytes([3]) * 32)


def fx(name: str) -> bytes:
    return base64.b64decode((FIX / name).read_text().strip())


def pd(raw: bytes) -> str:
    return "Program data: " + base64.b64encode(raw).decode()


def tx(program: str, *blobs: bytes) -> list[str]:
    return [f"Program {program} invoke [1]", *[pd(b) for b in blobs], f"Program {program} success"]


def create_pool_blob() -> bytes:
    raw = bytearray(bytes.fromhex("b1310cd2a076a774") + bytes(300))
    raw[173:205], raw[50:82], raw[82:114] = td.b58decode(POOL), td.b58decode(MINT), td.b58decode(WSOL)
    return bytes(raw)


def create_event_blob() -> bytes:
    def s(x: bytes) -> bytes:
        return len(x).to_bytes(4, "little") + x

    out = bytearray(bytes.fromhex("1b72a94ddeeb6376") + s(b"tok") + s(b"SYM") + s(b"https://x"))
    out += td.b58decode(MINT) + td.b58decode(CURVE) + td.b58decode(USER) + td.b58decode(USER)
    out += (1_790_318_000).to_bytes(8, "little", signed=True)
    out += (1_073_000_000_000_000).to_bytes(8, "little") + (30_000_000_000).to_bytes(8, "little") + (793_100_000_000_000).to_bytes(8, "little")
    out += (1_000_000_000_000_000).to_bytes(8, "little")
    out += bytes(32) + bytes([0, 0]) + bytes(32)
    return bytes(out)


def complete_event_blob() -> bytes:
    return bytes.fromhex("5f72619cd42e9808") + td.b58decode(USER) + td.b58decode(MINT) + td.b58decode(CURVE) + (1_790_318_100).to_bytes(8, "little", signed=True) + bytes(32)


BUY, SELL, BOND = fx("pumpswap_buy_event.b64"), fx("pumpswap_sell_event.b64"), fx("pump_trade_event.b64")


def clean_sample() -> list[list[str]]:
    return [tx(td.PUMPSWAP_PROGRAM, BUY), tx(td.PUMPSWAP_PROGRAM, SELL), tx(td.PUMP_BONDING_PROGRAM, BOND),
            tx(td.PUMP_BONDING_PROGRAM, create_event_blob()), tx(td.PUMPSWAP_PROGRAM, create_pool_blob()), tx(td.PUMP_BONDING_PROGRAM, complete_event_blob())]


def run(sample=None, **kw):
    return P.parity_report(clean_sample() if sample is None else sample, min_txs=kw.pop("min_txs", 1), **kw)


def test_clean_sample_is_exact_parity_and_counts_are_what_was_fed():
    r = run()
    assert r["failures"] == [] and r["verdict"] == "PASS" and r["tx_errors"] == {}
    c = r["counts"]
    assert c["walker_trade_pumpswap"] == 2 and c["walker_trade_pump_bonding"] == 1 and c["tape_trade"] == 3
    assert c["tape_create"] == 1 and c["tape_complete"] == 1 and c["tape_pool_create"] == 1
    assert c["adapter_market_pumpswap"] == 2 and c["adapter_trade_pump_bonding"] == 1  # PumpSwap rows whose pool is unresolved stay market-only: never guessed


def test_a_resolved_pumpswap_pool_maps_as_a_trade_row():
    """CreatePool first, then a buy of the SAME pool: the pool cache is shared across the sample like the live feed's."""
    ev = td.decode_program_data(BUY, event_v=True)
    blob = bytearray(create_pool_blob())
    blob[173:205] = td.b58decode(ev["pool"])
    r = run([tx(td.PUMPSWAP_PROGRAM, bytes(blob)), tx(td.PUMPSWAP_PROGRAM, BUY), tx(td.PUMP_BONDING_PROGRAM, BOND)])
    assert r["failures"] == [] and r["counts"]["adapter_trade_pumpswap"] == 1 and r["counts"].get("adapter_market_pumpswap", 0) == 0


def test_parity_failures_is_the_adapter_check_callable_and_returns_the_report_failures():
    assert P.parity_failures(clean_sample(), min_txs=1) == []
    assert P.parity_failures([]) != []


def test_empty_short_and_one_venue_samples_are_no_evidence():
    assert run([])["failures"] == ["no evidence: the sample is empty", "no evidence: no pumpswap trade reached the adapter", "no evidence: no pump_bonding trade reached the adapter"]
    r = P.parity_report(clean_sample())  # the default bar is a3_redeploy_check.SAMPLE_MIN_TXS
    assert r["min_txs"] == ARC.SAMPLE_MIN_TXS and r["failures"] == [f"no evidence: 6 transactions, need {ARC.SAMPLE_MIN_TXS}"]
    assert run([tx(td.PUMP_BONDING_PROGRAM, BOND)])["failures"] == ["no evidence: no pumpswap trade reached the adapter"]
    assert run([tx(td.PUMPSWAP_PROGRAM, BUY)])["failures"] == ["no evidence: no pump_bonding trade reached the adapter"]


def test_a_500_transaction_sample_passes_the_default_bar():
    assert P.parity_failures(clean_sample() * (ARC.SAMPLE_MIN_TXS // 6 + 1)) == []


def test_new_layout_without_the_v_tail_is_an_adapter_reject_and_a_fail():
    ev = td.decode_program_data(BUY[:441], event_v=True)
    assert ev is not None and "virtual_quote_reserves" not in ev  # a layout the walker still decodes but without the event-V tail
    r = run([tx(td.PUMPSWAP_PROGRAM, BUY[:441]), tx(td.PUMP_BONDING_PROGRAM, BOND)])
    assert r["verdict"] == "FAIL" and "adapter.reject:bad_virtual_quote_reserves x1" in r["failures"]
    assert "no evidence: no pumpswap trade reached the adapter" in r["failures"]


def test_short_bonding_event_loses_the_seven_extra_fields_and_fails():
    r = run([tx(td.PUMPSWAP_PROGRAM, BUY), tx(td.PUMP_BONDING_PROGRAM, BOND[:200])])
    assert r["verdict"] == "FAIL"
    assert r["failures"] == [f"trade.key_missing:{k} x1" for k in ("creator", "creator_fee_bps", "protocol_fee_bps", "real_base_reserve", "real_quote_reserve")]


def drift(monkeypatch, fn):
    real = AF.rows_from_notice

    def patched(note, pool_mints, counters=None):
        return fn(real(note, pool_mints, counters))

    monkeypatch.setattr(AF, "rows_from_notice", patched)


def test_a_changed_value_in_the_mapping_is_named_by_column(monkeypatch):
    def bump(rows):
        return [dict(r, sol_lamports=r["sol_lamports"] + 1) if r["type"] == "trade" else r for r in rows]

    drift(monkeypatch, bump)
    r = run()
    assert r["verdict"] == "FAIL" and "trade.value:sol_lamports x3" in r["failures"]


def test_a_dropped_tape_row_is_a_count_failure(monkeypatch):
    drift(monkeypatch, lambda rows: [r for r in rows if r["type"] != "trade" or r["side"] != "sell"])
    r = run()
    assert r["verdict"] == "FAIL" and any(x.startswith("trade.count_walker_vs_tape") for x in r["failures"])


def test_an_extra_and_a_missing_key_are_named(monkeypatch):
    drift(monkeypatch, lambda rows: [dict({k: v for k, v in r.items() if k != "trader"}, surprise=1) if r["type"] == "trade" else r for r in rows])
    f = run()["failures"]
    assert "trade.key_extra:surprise x3" in f and "trade.key_missing:trader x3" in f


def test_a_bad_side_is_an_adapter_reject_never_a_pass(monkeypatch):
    drift(monkeypatch, lambda rows: [dict(r, side="hold") if r["type"] == "trade" else r for r in rows])
    f = run()["failures"]
    assert "adapter.reject:bad_side x3" in f and "trade.adapter_rows_per_tape_row x3" in f


def test_create_without_a_bool_mayhem_flag_fails(monkeypatch):
    def bad(rows):
        out = []
        for r in rows:
            if r["type"] == "create":
                r = dict(r, is_mayhem_mode=None)
            out.append(r)
        return out

    drift(monkeypatch, bad)
    r = run()
    assert r["verdict"] == "FAIL" and "create.is_mayhem_mode:null x1" in r["failures"]


def test_a_raising_decode_is_a_failure_with_the_class_name_only(monkeypatch):
    def boom(*a, **k):
        raise KeyError("signature S123 secret-looking value")

    monkeypatch.setattr(td, "records_from_logs", boom)
    r = run()
    assert "decode raised KeyError on 6 transactions" in r["failures"] and all("S123" not in f for f in r["failures"])


def test_failure_strings_carry_no_value_of_the_sample(monkeypatch):
    drift(monkeypatch, lambda rows: [dict(r, sol_lamports=1) if r["type"] == "trade" else r for r in rows])
    ev = td.decode_program_data(BUY, event_v=True)
    text = " ".join(run()["failures"] + [str(run()["counts"])])
    for v in (ev["pool"], ev["trader"], MINT, POOL, USER):
        assert v not in text


def test_the_pinned_modules_are_imported_not_copied():
    assert P.AF is AF and P.FA.FeedAdapter.__module__ == "tools.r4b_feed_adapter" and P.td is td and P.L.TRADE_COLS["venue"] == "VARCHAR"
    assert P.BONDING_EXTRA_KEYS == frozenset({"real_quote_reserve", "real_base_reserve", "protocol_fee_bps", "protocol_fee", "creator", "creator_fee_bps", "creator_fee"})


# ---- wired into the decode check (the other terms stubbed as in test_r4bt_decode_check)
WRITE = {"name": "pump", "slot": 1000}
PINS = {"migrate": {"required_events": ["Complete"]}, "trailing_bytes": {}, "ignored_discs": {}}
R4PINS = {"frozen_sha": "a" * 40, "frozen_blobs": {"observe/trade_decode.py": "x"}}
T0 = 1_794_100_000_000


@pytest.fixture
def stubs(monkeypatch):
    big = clean_sample() * (ARC.SAMPLE_MIN_TXS // 6 + 1)
    ev = {"seen_events": list(ARC.REQUIRED_EVENTS) + ["Complete"], "undecodable": 0, "unknown": {}, "idl_mismatch": 0, "layout_mismatch": 0, "disc_len": {}, "capped": 0,
          "n_swap_events": 0}
    monkeypatch.setattr(ARC, "frozen_guard", lambda p: dict(p["frozen_blobs"]))
    monkeypatch.setattr(ARC, "fetch_idls", lambda http_get, pins: ({}, []))
    monkeypatch.setattr(ARC, "idl_event_table", lambda idls: {})
    monkeypatch.setattr(ARC, "validate_trailing_pins", lambda t: {})
    monkeypatch.setattr(ARC, "fetch_sample", lambda client, slot: (big, 100))
    monkeypatch.setattr(ARC, "evaluate_sample", lambda *a, **k: dict(ev))
    monkeypatch.setattr(ARC, "migrate_evidence", lambda *a, **k: dict(ev))
    monkeypatch.setattr(ARC, "sample_failures", lambda *a, **k: None)


def check(adapter):
    return D.check_write(WRITE, PINS, R4PINS, client=None, http_get=None, now_ms=lambda: T0, deploy_ms=T0 - 3_600_000, adapter_check=adapter)


def test_decode_check_passes_with_the_parity_function_and_the_a3_classifier_accepts_the_file(stubs):
    r = check(P.parity_failures)
    assert r["verdict"] == "PASS" and r["reasons"] == []
    assert D.collect([r])["profile"] == A.PROFILE


def test_decode_check_fails_when_the_mapping_drifts(stubs, monkeypatch):
    drift(monkeypatch, lambda rows: [dict(r, token_raw=0) if r["type"] == "trade" else r for r in rows])
    r = check(P.parity_failures)
    assert r["verdict"] == "FAIL" and any(x.startswith("c: adapter path: trade.value:token_raw") for x in r["reasons"])


def test_two_runs_share_no_state():
    a, b = P._State(), P._State()
    a.bad["x"] += 1
    assert b.bad == collections.Counter() and a.adapter is not b.adapter
