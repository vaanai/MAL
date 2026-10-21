"""Tests for tools/a3_redeploy_check.py. Fixture-based: no network (fake RPC client, fake HTTP). The IDL fixture is a trimmed copy of the pump / pump_amm field lists
(pump-public-docs main, read 2026-10-21); the event bytes are built from it by an encoder in this file."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path

import pytest

from observe import trade_decode as td
from tools import a3_redeploy_check as c
from tools import pump_structure_monitor as mon

DEPLOY_PUMP = "2026-10-19T17:47:02Z"
DEPLOY_SWAP = "2026-10-19T17:47:20Z"
RUN_UTC = "2026-10-20T07:11:27Z"
PUMP, SWAP = td.PUMP_BONDING_PROGRAM, td.PUMPSWAP_PROGRAM


def ms(t: str) -> int:
    return c.utc_ms(t)


# ---------------------------------------------------------------- IDL fixture and encoder
def fields(spec: str):
    out = []
    for item in spec.split():
        n, t = item.split(":")
        out.append({"name": n, "type": {"vec": {"defined": {"name": "Shareholder"}}} if t == "vecsh" else t})
    return out


EVENTS = {
    "pump": {
        "TradeEvent": "mint:pubkey sol_amount:u64 token_amount:u64 is_buy:bool user:pubkey timestamp:i64 virtual_sol_reserves:u64 virtual_token_reserves:u64 real_sol_reserves:u64 "
                      "real_token_reserves:u64 fee_recipient:pubkey fee_basis_points:u64 fee:u64 creator:pubkey creator_fee_basis_points:u64 creator_fee:u64 track_volume:bool "
                      "total_unclaimed_tokens:u64 total_claimed_tokens:u64 current_sol_volume:u64 last_update_timestamp:i64 ix_name:string mayhem_mode:bool "
                      "cashback_fee_basis_points:u64 cashback:u64 buyback_fee_basis_points:u64 buyback_fee:u64 shareholders:vecsh quote_mint:pubkey quote_amount:u64",
        "PostCompleteBuyEvent": "user:pubkey mint:pubkey bonding_curve:pubkey quote_mint:pubkey timestamp:i64 base_out:u64 quote_in:u64 fee_basis_points:u64 fee:u64 "
                                "creator_fee_basis_points:u64 creator_fee:u64 buyback_fee:u64 pool_base_reserves_before:u64 pool_quote_reserves_before:u64 "
                                "pool_base_reserves_after:u64 pool_quote_reserves_after:u64",
    },
    "pump_amm": {
        "BuyEvent": "timestamp:i64 base_amount_out:u64 max_quote_amount_in:u64 user_base_token_reserves:u64 user_quote_token_reserves:u64 pool_base_token_reserves:u64 "
                    "pool_quote_token_reserves:u64 quote_amount_in:u64 lp_fee_basis_points:u64 lp_fee:u64 protocol_fee_basis_points:u64 protocol_fee:u64 "
                    "quote_amount_in_with_lp_fee:u64 user_quote_amount_in:u64 pool:pubkey user:pubkey user_base_token_account:pubkey user_quote_token_account:pubkey "
                    "protocol_fee_recipient:pubkey protocol_fee_recipient_token_account:pubkey coin_creator:pubkey coin_creator_fee_basis_points:u64 coin_creator_fee:u64 "
                    "track_volume:bool total_unclaimed_tokens:u64 total_claimed_tokens:u64 current_sol_volume:u64 last_update_timestamp:i64 min_base_amount_out:u64 "
                    "ix_name:string cashback_fee_basis_points:u64 cashback:u64 buyback_fee_basis_points:u64 buyback_fee:u64 virtual_quote_reserves:i128 can_boost:bool "
                    "base_supply:u64 holder_rewards_bps:u64 holder_rewards:u64 creator_fee_unclaimed:u64",
        "SellEvent": "timestamp:i64 base_amount_in:u64 min_quote_amount_out:u64 user_base_token_reserves:u64 user_quote_token_reserves:u64 pool_base_token_reserves:u64 "
                     "pool_quote_token_reserves:u64 quote_amount_out:u64 lp_fee_basis_points:u64 lp_fee:u64 protocol_fee_basis_points:u64 protocol_fee:u64 "
                     "quote_amount_out_without_lp_fee:u64 user_quote_amount_out:u64 pool:pubkey user:pubkey user_base_token_account:pubkey user_quote_token_account:pubkey "
                     "protocol_fee_recipient:pubkey protocol_fee_recipient_token_account:pubkey coin_creator:pubkey coin_creator_fee_basis_points:u64 coin_creator_fee:u64 "
                     "cashback_fee_basis_points:u64 cashback:u64 buyback_fee_basis_points:u64 buyback_fee:u64 virtual_quote_reserves:i128 can_boost:bool base_supply:u64 "
                     "holder_rewards_bps:u64 holder_rewards:u64 creator_fee_unclaimed:u64",
        "InitBoostEvent": "timestamp:i64 mint:pubkey bonding_curve:pubkey pool:pubkey virtual_quote_reserves:i128 real_quote_reserves_after:u64",
        "CreatePoolEvent": "timestamp:i64 index:u16 creator:pubkey base_mint:pubkey quote_mint:pubkey base_mint_decimals:u8 quote_mint_decimals:u8 base_amount_in:u64 "
                           "quote_amount_in:u64 pool_base_amount:u64 pool_quote_amount:u64 minimum_liquidity:u64 initial_liquidity:u64 lp_token_amount_out:u64 pool_bump:u8 "
                           "pool:pubkey lp_mint:pubkey user_base_token_account:pubkey user_quote_token_account:pubkey coin_creator:pubkey is_mayhem_mode:bool "
                           "creator_fee_bps:u64 can_edit_creator_fee:bool is_holder_reward:bool",
    },
}


def make_idls():
    idls = {}
    for prog, evs in EVENTS.items():
        types = [{"name": "Shareholder", "type": {"kind": "struct", "fields": fields("address:pubkey share_bps:u16")}}]
        events = []
        for name, spec in evs.items():
            types.append({"name": name, "type": {"kind": "struct", "fields": fields(spec)}})
            events.append({"name": name, "discriminator": list(hashlib.sha256(f"event:{name}".encode()).digest()[:8])})
        idls[prog] = {"metadata": {"name": prog}, "events": events, "types": types}
    idls["pump_fees"] = {"metadata": {"name": "pump_fees"}, "events": [], "types": []}
    return idls


IDLS = make_idls()
IDL_BYTES = {k: json.dumps(v, sort_keys=True).encode() for k, v in IDLS.items()}


def encode(name: str, *, prog: str, trunc: int | None = None, ix: str = "buy") -> bytes:
    entry = {e["name"]: e for e in IDLS[prog]["events"]}[name]
    out = bytearray(bytes(entry["discriminator"]))
    for i, f in enumerate(IDLS[prog]["types"][[t["name"] for t in IDLS[prog]["types"]].index(name)]["type"]["fields"]):
        t = f["type"]
        if t == "bool":
            out += b"\x01" if f["name"] != "track_volume" else b"\x00"
        elif t == "pubkey":
            out += bytes([i + 1]) * 32
        elif t == "u64":
            out += struct.pack("<Q", 1000 + i)
        elif t == "i64":
            out += struct.pack("<q", 1_790_000_000)
        elif t == "i128":
            out += (5000 + i).to_bytes(16, "little", signed=True)
        elif t == "u8":
            out += b"\x02"
        elif t == "u16":
            out += b"\x03\x00"
        elif t == "string":
            out += struct.pack("<I", len(ix)) + ix.encode()
        else:
            out += struct.pack("<I", 0)
    return bytes(out if trunc is None else out[:trunc])


def logs_for(prog_id: str, blob: bytes) -> list[str]:
    return [f"Program {prog_id} invoke [1]", "Program data: " + base64.b64encode(blob).decode(), f"Program {prog_id} success"]


def good_blobs():
    return [("TradeEvent", "pump", PUMP), ("BuyEvent", "pump_amm", SWAP), ("SellEvent", "pump_amm", SWAP), ("InitBoostEvent", "pump_amm", SWAP), ("CreatePoolEvent", "pump_amm", SWAP)]


def sample_blobs(extra=()):
    base = [(encode(n, prog=p), pid) for n, p, pid in good_blobs()]
    return base + list(extra)


class FakeClient:
    def __init__(self, blobs, per_block=100, fail=None):
        self.blobs, self.per_block, self.fail, self.calls = blobs, per_block, fail, 0

    def call(self, method, params):
        self.calls += 1
        if self.fail:
            raise self.fail
        txs = []
        for i in range(self.per_block):
            blob, pid = self.blobs[i % len(self.blobs)]
            txs.append({"meta": {"err": None, "logMessages": logs_for(pid, blob)}})
        return {"transactions": txs}


def http_ok(overrides=None):
    def get(url, timeout):
        name = url.rsplit("/", 1)[1][:-5]
        body = (overrides or {}).get(name, IDL_BYTES[name])
        return 200, {}, body
    return get


def build_pins(blobs=None):
    blobs = blobs or sample_blobs()
    lens = {}
    for b, _ in blobs:
        h = b[:8].hex()
        lens[h] = {"min_len": len(b) - 5, "max_len": len(b) + 5}
    return {"idl": {k: {"sha256": hashlib.sha256(v).hexdigest()} for k, v in IDL_BYTES.items()}, "disc_len": lens,
            "boost": {"init_boost_share": 0.9, "min_init_boost_in_sample": 1}}


MON_PINS = {
    "accounts": {"bonding_fee_config": {"sha256": "a" * 64}, "pumpswap_fee_config": {"sha256": "b" * 64}, "pumpswap_global_config": {"sha256": "c" * 64}},
    "programs": {"pump": {"deploy_slot": 100, "deploy_utc": "2026-10-13T15:40:41Z"}, "pumpswap": {"deploy_slot": 200, "deploy_utc": "2026-10-08T16:20:03Z"},
                 "fees": {"deploy_slot": 300, "deploy_utc": "2026-10-12T17:40:24Z"}},
    "boost": {"budget_sol": 17.585, "slices": 29},
}


def flag(halt=False, evaluated=True, reason="ok"):
    return {"halt": halt, "evaluated": evaluated, "reason": reason}


def make_rec(*, redeploy=True, run_utc=RUN_UTC, **over):
    flags = {k: flag() for k in c.ALL_HALT_FLAGS}
    rec = {
        "run_utc": run_utc,
        "halt": {"flags": flags},
        "accounts": {n: {"pinned": True, "sha256": p["sha256"]} for n, p in MON_PINS["accounts"].items()},
        "programs": {"pump": {"deploy_slot": 100, "deploy_utc": "2026-10-13T15:40:41Z"}, "pumpswap": {"deploy_slot": 200, "deploy_utc": "2026-10-08T16:20:03Z"},
                     "fees": {"deploy_slot": 300, "deploy_utc": "2026-10-12T17:40:24Z"}},
        "graduations": {"boost_denominator": {"n_nonmayhem_wsol": 20, "n_init_boost": 18}},
        "boost": {"budget_sol": {"median": 17.6}, "slices": {"median": 29}},
    }
    if redeploy:
        rec["programs"]["pump"] = {"deploy_slot": 1000, "deploy_utc": DEPLOY_PUMP}
        rec["programs"]["pumpswap"] = {"deploy_slot": 1100, "deploy_utc": DEPLOY_SWAP}
        flags["pins_changed"] = flag(True, reason="program_pump deploy_slot 100 -> 1000; program_pumpswap deploy_slot 200 -> 1100")
    for k, v in over.items():
        rec[k] = v
    return rec


NOW = ms("2026-10-20T07:20:00Z")


def run(rec, tmp_path, *, prev=None, pins=None, client=None, http=None, now=None, state=None):
    clock = now or (lambda: NOW)
    return c.decide(rec, prev, MON_PINS, pins if pins is not None else build_pins(), state or tmp_path / "state.jsonl",
                    client=client if client is not None else FakeClient(sample_blobs()), http_get=http or http_ok(), now_ms=clock, job_id="job-1")


# ---------------------------------------------------------------- tests
def test_no_flag_is_none(tmp_path):
    v = run(make_rec(redeploy=False), tmp_path)
    assert v["verdict"] == "NONE" and v["halt_at_ms"] is None and v["flag_halt_ms"] is None


def test_pass_path_is_record_only_and_discloses(tmp_path):
    cl = FakeClient(sample_blobs())
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "PASS", v["reasons"]
    assert v["record_only"] and v["halt_at_ms"] is None
    assert {r["name"]: r["slot"] for r in v["redeploys"]} == {"pump": 1000, "pumpswap": 1100}
    assert v["flag_halt_ms"] == ms(RUN_UTC)  # old-rule books still see the flag
    assert v["detail"]["sample_start_slot"] == 1100 + c.SAMPLE_START_OFFSET_SLOTS
    assert v["detail"]["n_txs"] == c.SAMPLE_MIN_TXS
    assert cl.calls == 5  # 100 txs per block
    assert v["redeploy_utc"] == DEPLOY_PUMP and v["deadline_utc"] == "2026-10-20T17:47:02Z"
    st = c.read_state(tmp_path / "state.jsonl")
    assert set(st) == {"pump:1000", "pumpswap:1100"} and st["pump:1000"]["job_id"] == "job-1" and st["pump:1000"]["verdict"] == "PASS"


def test_passed_slot_is_covered_not_reclassified(tmp_path):
    run(make_rec(), tmp_path)
    boom = FakeClient([], fail=AssertionError("must not fetch"))
    v = run(make_rec(run_utc="2026-10-21T07:11:27Z"), tmp_path, client=boom, http=lambda *a: (_ for _ in ()).throw(AssertionError("no http")))
    assert v["verdict"] == "PASS" and v.get("covered") and v["halt_at_ms"] is None


def test_new_slot_after_a_pass_is_checked_again(tmp_path):
    run(make_rec(), tmp_path)
    rec = make_rec(run_utc="2026-10-22T07:11:27Z")
    rec["programs"]["pump"] = {"deploy_slot": 5000, "deploy_utc": "2026-10-22T01:00:00Z"}
    cl = FakeClient(sample_blobs())
    v = run(rec, tmp_path, client=cl, now=lambda: ms("2026-10-22T07:20:00Z"))
    assert v["verdict"] == "PASS" and not v.get("covered") and cl.calls > 0


FAIL_CASES = {}


def _case(name):
    def deco(fn):
        FAIL_CASES[name] = fn
        return fn
    return deco


@_case("a_boost_flag_not_evaluated")
def _a(rec, pins, kw):
    rec["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)
    return "a: boost_share_low not evaluated"


@_case("a_flag_absent")
def _a2(rec, pins, kw):
    del rec["halt"]["flags"]["synthetic_share_high"]
    return "a: flag synthetic_share_high absent"


@_case("e_share_off")
def _e1(rec, pins, kw):
    rec["graduations"]["boost_denominator"] = {"n_nonmayhem_wsol": 20, "n_init_boost": 12}  # 0.6 vs 0.9
    return "e: InitBoost share"


@_case("e_budget_off")
def _e2(rec, pins, kw):
    rec["boost"]["budget_sol"] = {"median": 22.0}
    return "e: BOOST budget median"


@_case("e_slices_off")
def _e3(rec, pins, kw):
    rec["boost"]["slices"] = {"median": 40}
    return "e: BOOST slices median"


@_case("e_few_init_boost_events")
def _e4(rec, pins, kw):
    pins["boost"]["min_init_boost_in_sample"] = 10_000
    return "e: InitBoost events in sample"


@_case("f_idl_changed")
def _f(rec, pins, kw):
    changed = json.loads(IDL_BYTES["pump_amm"])
    changed["metadata"]["version"] = "9"
    kw["http"] = http_ok({"pump_amm": json.dumps(changed).encode()})
    return "f: idl pump_amm sha256"


@_case("c_unknown_discriminator")
def _c1(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs([(b"\x01\x02\x03\x04\x05\x06\x07\x08" + b"\x00" * 40, SWAP)]))
    return "c: unknown discriminators"


@_case("c_undecodable_known_event")
def _c2(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs([(encode("SellEvent", prog="pump_amm", trunc=100), SWAP)]))
    return "c: undecodable events"


@_case("c_shadow_differs_from_idl")
def _c3(rec, pins, kw):
    real = c.shadow_decode

    def bad(raw):
        row = real(raw)
        if row is not None and row.get("kind") == "trade" and row["side"] == "buy" and row["venue"] == "pumpswap":
            row = dict(row, sol_lamports=row["sol_lamports"] + 1)
        return row
    kw["patch"] = ("shadow_decode", bad)
    return "c: shadow decode differs from the IDL decode"


@_case("c_sample_short")
def _c4(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs(), per_block=1)  # 300 slots x 1 tx
    return "c: sample has 300 txs"


@_case("d_length_outside_pin")
def _d1(rec, pins, kw):
    h = encode("BuyEvent", prog="pump_amm")[:8].hex()
    pins["disc_len"][h] = {"min_len": 10, "max_len": 20}
    return "d: discriminator"


@_case("d_required_event_missing")
def _d2(rec, pins, kw):
    kw["client"] = FakeClient([b for b in sample_blobs() if b[0][:8] != td._SELL_DISC])
    return "d: SellEvent not seen"


@pytest.mark.parametrize("case", sorted(FAIL_CASES))
def test_fail_reasons(case, tmp_path, monkeypatch):
    rec, pins, kw = make_rec(), build_pins(), {}
    want = FAIL_CASES[case](rec, pins, kw)
    if "patch" in kw:
        monkeypatch.setattr(c, kw["patch"][0], kw["patch"][1])
    v = run(rec, tmp_path, pins=pins, client=kw.get("client"), http=kw.get("http"))
    assert v["verdict"] == "FAIL", v
    assert any(want in r for r in v["reasons"]), v["reasons"]
    assert v["halt_at_ms"] == ms(DEPLOY_PUMP) and not v["record_only"]  # the REDEPLOY instant, the earlier of the two


def test_halt_instant_is_earliest_changed_redeploy(tmp_path):
    rec = make_rec()
    rec["programs"]["pumpswap"] = {"deploy_slot": 1100, "deploy_utc": "2026-10-19T17:40:00Z"}
    rec["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)
    v = run(rec, tmp_path)
    assert v["verdict"] == "FAIL" and v["halt_at_ms"] == ms("2026-10-19T17:40:00Z")


def test_missing_pin_is_error_and_halts_at_redeploy(tmp_path):
    pins = build_pins()
    pins["idl"]["pump"]["sha256"] = None
    v = run(make_rec(), tmp_path, pins=pins)
    assert v["verdict"] == "ERROR" and "pin missing" in v["reasons"][0] and v["halt_at_ms"] == ms(DEPLOY_PUMP)
    # the shipped stub has nulls everywhere: it can never PASS
    stub = json.loads((Path(c.__file__).with_name("a3_redeploy_pins.json")).read_text())
    assert run(make_rec(), tmp_path / "x", pins=stub)["verdict"] == "ERROR"


def test_idl_fetch_failure_is_error(tmp_path):
    v = run(make_rec(), tmp_path, http=lambda url, t: (503, {}, b""))
    assert v["verdict"] == "ERROR" and v["halt_at_ms"] == ms(DEPLOY_PUMP)


@pytest.mark.parametrize("exc", [mon.RpcError("boom"), mon.RpcUnreachable("down"), mon.CallBudgetExceeded("cap")])
def test_rpc_failure_is_error(tmp_path, exc):
    v = run(make_rec(), tmp_path, client=FakeClient([], fail=exc))
    assert v["verdict"] == "ERROR" and v["halt_at_ms"] == ms(DEPLOY_PUMP)


def test_unmapped_idl_layout_is_error_not_pass(tmp_path):
    idl = json.loads(IDL_BYTES["pump_amm"])
    for t in idl["types"]:
        if t["name"] == "BuyEvent":
            t["type"]["fields"] = [f for f in t["type"]["fields"] if f["name"] != "coin_creator_fee"]
    pins = build_pins()
    pins["idl"]["pump_amm"]["sha256"] = hashlib.sha256(json.dumps(idl).encode()).hexdigest()
    v = run(make_rec(), tmp_path, pins=pins, http=http_ok({"pump_amm": json.dumps(idl).encode()}))
    assert v["verdict"] == "FAIL" and any("c: shadow decode differs" in r for r in v["reasons"])


def test_timeout_when_started_late(tmp_path):
    late = ms("2026-10-20T17:47:03Z")  # 24 h and 1 s after the pump redeploy
    cl = FakeClient([], fail=AssertionError("must not run the check"))
    v = run(make_rec(), tmp_path, client=cl, now=lambda: late)
    assert v["verdict"] == "FAIL" and "timeout" in v["reasons"][0] and v["halt_at_ms"] == ms(DEPLOY_PUMP)


def test_timeout_when_finished_late_even_if_all_pass(tmp_path):
    calls = []

    def clock():  # on time at the start, past the deadline from the second reading on
        calls.append(1)
        return NOW if len(calls) == 1 else ms("2026-10-20T17:50:00Z")
    v = run(make_rec(), tmp_path, now=clock)
    assert v["verdict"] == "FAIL" and "timeout: finished" in v["reasons"][0] and v["halt_at_ms"] == ms(DEPLOY_PUMP)
    assert c.read_state(tmp_path / "state.jsonl")["pump:1000"]["verdict"] == "FAIL"


def test_fail_is_sticky_and_a_later_clean_run_cannot_pass_it(tmp_path):
    rec = make_rec()
    rec["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)
    assert run(rec, tmp_path)["verdict"] == "FAIL"
    v = run(make_rec(run_utc="2026-10-21T07:11:27Z"), tmp_path)  # all clean now, same slots
    assert v["verdict"] == "FAIL" and v["halt_at_ms"] == ms(DEPLOY_PUMP) and v["reasons"][0].startswith("state:")


# ---- hard halts: always, no check, no network
def hard(rec, tmp_path, prev=None):
    cl = FakeClient([], fail=AssertionError("a hard halt must not run the check"))
    return run(rec, tmp_path, prev=prev, client=cl, http=lambda *a: (_ for _ in ()).throw(AssertionError("no http")))


@pytest.mark.parametrize("name", list(c.BOOST_FLAGS) + ["synthetic_share_high"])
def test_boost_and_synthetic_flags_hard_halt_at_flag_instant(name, tmp_path):
    rec = make_rec()
    rec["halt"]["flags"][name] = flag(True, reason="x")
    v = hard(rec, tmp_path)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(RUN_UTC) and v["reasons"][0].startswith(name)


def test_boost_flag_alone_hard_halts(tmp_path):
    rec = make_rec(redeploy=False)
    rec["halt"]["flags"]["boost_disabled"] = flag(True)
    assert hard(rec, tmp_path)["halt_at_ms"] == ms(RUN_UTC)


@pytest.mark.parametrize("acct", ["bonding_fee_config", "pumpswap_fee_config", "pumpswap_global_config"])
def test_fee_or_global_config_change_hard_halts(acct, tmp_path):
    rec = make_rec()
    rec["accounts"][acct]["sha256"] = "f" * 64
    v = hard(rec, tmp_path)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(RUN_UTC)
    assert any(acct in r for r in v["reasons"])


def test_fees_program_redeploy_hard_halts_even_with_pump(tmp_path):
    rec = make_rec()
    rec["programs"]["fees"] = {"deploy_slot": 999, "deploy_utc": "2026-10-19T20:00:00Z"}
    v = hard(rec, tmp_path)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(RUN_UTC) and any("fees" in r for r in v["reasons"])


def test_unevaluated_on_two_consecutive_runs_hard_halts(tmp_path):
    rec, prev = make_rec(redeploy=False), make_rec(redeploy=False, run_utc="2026-10-19T07:11:27Z")
    for r in (rec, prev):
        r["halt"]["flags"]["boost_last_slice_early"] = flag(False, evaluated=False)
    assert hard(rec, tmp_path, prev=prev)["verdict"] == "HALT"
    prev["halt"]["flags"]["boost_last_slice_early"] = flag()  # only one run unevaluated: not a hard halt
    assert run(rec, tmp_path, prev=prev)["verdict"] == "NONE"


def test_pins_changed_without_typed_difference_hard_halts(tmp_path):
    rec = make_rec(redeploy=False)
    rec["halt"]["flags"]["pins_changed"] = flag(True, reason="something")
    assert hard(rec, tmp_path)["verdict"] == "HALT"


def test_triage_old_rule_instant_set_for_any_flag():
    t = c.triage(make_rec(), MON_PINS, None)
    assert t["action"] == "redeploy" and t["flag_halt_ms"] == ms(RUN_UTC)
    assert c.triage(make_rec(redeploy=False), MON_PINS, None)["flag_halt_ms"] is None


# ---- decode helpers
def test_program_data_only_from_pump_programs():
    blob = encode("TradeEvent", prog="pump")
    other = ["Program JUP invoke [1]", "Program data: " + base64.b64encode(blob).decode(), "Program JUP success"]
    nested = [f"Program JUP invoke [1]", f"Program {PUMP} invoke [2]", "Program data: " + base64.b64encode(blob).decode(), f"Program {PUMP} success",
              "Program data: " + base64.b64encode(blob).decode(), "Program JUP success"]
    assert c.program_data_events(other) == []
    assert c.program_data_events(nested) == [blob]  # the second line is JUP's, not pump's


def test_failed_txs_are_not_in_the_sample():
    blob = encode("TradeEvent", prog="pump")
    assert c.tx_is_sample({"meta": {"err": None, "logMessages": logs_for(PUMP, blob)}})
    assert not c.tx_is_sample({"meta": {"err": {"InstructionError": [0, "x"]}, "logMessages": logs_for(PUMP, blob)}})
    assert not c.tx_is_sample({"meta": {"err": None, "logMessages": logs_for("OTHER", blob)}})


def test_event_mapping_matches_shadow_on_every_fixture_event():
    table = c.idl_event_table({k: v for k, v in IDLS.items()})
    for name, prog, _ in good_blobs():
        raw = encode(name, prog=prog)
        assert c.compare_event(raw, table[raw[:8]], c.shadow_decode(raw)) == [], name


# ---- main(): exit codes, verdict file, no override
def write_daily(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def write_pins_files(tmp_path, pins):
    (tmp_path / "mp.json").write_text(json.dumps(MON_PINS))
    (tmp_path / "ap.json").write_text(json.dumps(pins))


def argv(tmp_path, *extra):
    return ["--daily", str(tmp_path / "daily.jsonl"), "--monitor-pins", str(tmp_path / "mp.json"), "--pins", str(tmp_path / "ap.json"), "--state", str(tmp_path / "st.jsonl"),
            "--verdict-out", str(tmp_path / "v.json"), *extra]


def test_main_pass_and_fail_exit_codes(tmp_path, capsys):
    write_pins_files(tmp_path, build_pins())
    write_daily(tmp_path / "daily.jsonl", [make_rec()])
    assert c.main(argv(tmp_path), client=FakeClient(sample_blobs()), http_get=http_ok(), now_ms=lambda: NOW) == 0
    assert json.loads((tmp_path / "v.json").read_text())["verdict"] == "PASS"
    bad = make_rec(run_utc="2026-10-21T07:11:27Z")
    bad["programs"]["pump"] = {"deploy_slot": 7777, "deploy_utc": "2026-10-21T01:00:00Z"}
    bad["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)
    write_daily(tmp_path / "daily.jsonl", [make_rec(), bad])
    assert c.main(argv(tmp_path), client=FakeClient(sample_blobs()), http_get=http_ok(), now_ms=lambda: ms("2026-10-21T07:15:00Z")) == 10
    # pump 1000 / pumpswap 1100 already PASSed (state), so only the new pump slot is pending: the halt is at ITS redeploy instant
    assert json.loads((tmp_path / "v.json").read_text())["halt_at_ms"] == ms("2026-10-21T01:00:00Z")


def test_main_stale_record_is_operational_error_not_a_pass(tmp_path, capsys):
    write_pins_files(tmp_path, build_pins())
    write_daily(tmp_path / "daily.jsonl", [make_rec(redeploy=False)])
    assert c.main(argv(tmp_path), client=FakeClient([]), http_get=http_ok(), now_ms=lambda: NOW + 3600_000) == 11
    assert not (tmp_path / "v.json").exists()
    assert c.main(["--daily", str(tmp_path / "missing.jsonl")], now_ms=lambda: NOW) == 11


def test_main_refuses_helius(tmp_path):
    assert c.main(["--rpc-url", "https://x.helius-rpc.com/?api-key=k"]) == 64


def test_there_is_no_override_option():
    p = c.parse_args(["--observe"])
    names = " ".join(vars(p))
    for word in ("override", "force", "skip", "ignore", "approve", "pass"):
        assert word not in names
    src = Path(c.__file__).read_text()
    assert src.count("os.environ") == 1 and 'os.environ.get("MISCUSI_JOB_ID", "")' in src  # the only environment read is the job id label


def test_observe_prints_pin_values_and_decides_nothing(tmp_path, capsys):
    write_pins_files(tmp_path, build_pins())
    write_daily(tmp_path / "daily.jsonl", [make_rec()])
    assert c.main(argv(tmp_path, "--observe"), client=FakeClient(sample_blobs()), http_get=http_ok(), now_ms=lambda: NOW) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["idl_sha256"]["pump"] == hashlib.sha256(IDL_BYTES["pump"]).hexdigest() and out["n_init_boost"] > 0 and not out["unknown"]
    assert not (tmp_path / "st.jsonl").exists()
