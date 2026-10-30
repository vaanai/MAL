"""Tests for tools/a3_redeploy_check.py. Fixture-based: no network (fake RPC client, fake HTTP). The IDL fixture is a trimmed copy of the pump / pump_amm field lists
(pump-public-docs main, read 2026-10-21); the event bytes are built from it by an encoder in this file."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
import subprocess
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
PIN_PUMP_UTC, PIN_SWAP_UTC = "2026-10-13T15:40:41Z", "2026-10-08T16:20:03Z"  # the monitor pins' own deploy instants


def ms(t: str) -> int:
    return c.utc_ms(t)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(c, "RETRY_SLEEP", slept.append)  # the null-retry backoff must not cost test time
    return slept


# ---------------------------------------------------------------- IDL fixture and encoder
def fields(spec: str):
    out = []
    for item in spec.split():
        n, t = item.split(":")
        out.append({"name": n, "type": {"vec": {"defined": {"name": "Shareholder"}}} if t == "vecsh" else t})
    return out


EVENTS = {
    "pump": {
        "CompleteEvent": "user:pubkey mint:pubkey bonding_curve:pubkey timestamp:i64 quote_mint:pubkey",
        "CompletePumpAmmMigrationEvent": "user:pubkey mint:pubkey mint_amount:u64 sol_amount:u64 pool_migration_fee:u64 bonding_curve:pubkey timestamp:i64 pool:pubkey quote_mint:pubkey",
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


def encode(name: str, *, prog: str, trunc: int | None = None, ix: str = "buy", over: dict | None = None, trail: bytes | None = None) -> bytes:
    entry = {e["name"]: e for e in IDLS[prog]["events"]}[name]
    out = bytearray(bytes(entry["discriminator"]))
    for i, f in enumerate(IDLS[prog]["types"][[t["name"] for t in IDLS[prog]["types"]].index(name)]["type"]["fields"]):
        t = f["type"]
        if over and f["name"] in over:
            out += over[f["name"]]
        elif t == "bool":
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
    if name in c.TRAILING_EVENTS:
        out += b"\x00" * 8 if trail is None else trail  # the live programs append 8 bytes (zero on SOL-quoted trades) the IDL does not list
    return bytes(out if trunc is None else out[:trunc])


def logs_for(prog_id: str, blob: bytes) -> list[str]:
    return [f"Program {prog_id} invoke [1]", "Program data: " + base64.b64encode(blob).decode(), f"Program {prog_id} success"]


def good_blobs():
    return [("TradeEvent", "pump", PUMP), ("BuyEvent", "pump_amm", SWAP), ("SellEvent", "pump_amm", SWAP), ("InitBoostEvent", "pump_amm", SWAP), ("CreatePoolEvent", "pump_amm", SWAP)]


def sample_blobs(extra=()):
    base = [(encode(n, prog=p), pid) for n, p, pid in good_blobs()]
    return base + list(extra)


MIN_SLOT = 1100 + c.SAMPLE_START_OFFSET_SLOTS
POOLS = [mon.b58encode(bytes([200 + i % 50, i]) * 16) for i in range(60)]


def migrate_tx(i: int, *, slot0: int = MIN_SLOT, init_boost: bool = True, budget_sol: float = 17.585, extra_blobs=(), drop=(), quote_wsol: bool = True) -> dict:
    """A constructed migrate tx: CompleteEvent / CompletePumpAmmMigration / CreatePool / InitBoost as emit_cpi inner instructions (the shape the shadow reads)."""
    pool = POOLS[i]
    mint = mon.b58encode(bytes([i + 1]) * 32)
    over = {"mint": mon.b58decode(mint), "pool": mon.b58decode(pool), "bonding_curve": bytes([90 + i]) * 32}
    if quote_wsol:
        over["quote_mint"] = bytes(32)  # native SOL, normalised to WSOL by the decoder
    blobs = [encode("CompleteEvent", prog="pump", over=over), encode("CompletePumpAmmMigrationEvent", prog="pump", over=over),
             encode("CreatePoolEvent", prog="pump_amm", over={"pool": mon.b58decode(pool)}),
             encode("InitBoostEvent", prog="pump_amm", over=over)]
    blobs = [b for b in blobs if b[:8] not in drop] + list(extra_blobs)
    vault = mon.boost_vault(pool)
    keys = ["Signer1111111111111111111111111111111111111", PUMP, SWAP, vault]
    inner = [{"index": 0, "instructions": [{"programIdIndex": 1 if j % 2 == 0 else 2, "data": mon.b58encode(td.ANCHOR_EVENT_IX_TAG + b)} for j, b in enumerate(blobs)]}]
    logs = [f"Program {PUMP} invoke [1]", "Program log: Instruction: Migrate", f"Program {PUMP} success"]
    if init_boost:
        logs += [f"Program {SWAP} invoke [1]", "Program log: Instruction: InitBoost", f"Program {SWAP} success"]
    pre = [10**9, 0, 0, 0]
    post = [10**9, 0, 0, int(budget_sol * 1e9) if init_boost else 0]
    return {"slot": slot0 + i, "blockTime": (NOW // 1000) - 7200 + i,
            "transaction": {"message": {"accountKeys": keys, "header": {"numRequiredSignatures": 1}}},
            "meta": {"err": None, "logMessages": logs, "innerInstructions": inner, "preBalances": pre, "postBalances": post}}


class FakeClient:
    """Fake public RPC. getBlock feeds the log sample; the migrate flow (signature list, whole txs, Pool accounts, BOOST vault authority signature lists) is served from `migrate_kw`."""

    def __init__(self, blobs, per_block=100, fail=None, n_migrate=c.MIGRATE_K, n_slices=29, mayhem=False, boost_every=1, budget_sol=17.585, migrate_extra=(), migrate_drop=(), old_sig_slot=1000, slot0=MIN_SLOT, pool_quote=mon.b58decode(mon.WSOL_MINT), fail_on=()):
        self.blobs, self.per_block, self.fail, self.calls = blobs, per_block, fail, 0
        self.txs = {}
        self.sigs = []
        for i in range(n_migrate):
            tx = migrate_tx(i, slot0=slot0, init_boost=(i % boost_every == 0), budget_sol=budget_sol, extra_blobs=migrate_extra, drop=migrate_drop)
            sig = f"sig{i}"
            self.txs[sig] = tx
            self.sigs.append({"signature": sig, "slot": tx["slot"], "blockTime": tx["blockTime"], "err": None})
        self.sigs.append({"signature": "old", "slot": old_sig_slot, "blockTime": (NOW // 1000) - 90000, "err": None})
        self.sigs.sort(key=lambda x: -x["slot"])
        self.n_slices, self.mayhem, self.pool_quote = n_slices, mayhem, pool_quote
        self.authorities = {mon.boost_vault_authority(POOLS[i]) for i in range(60)}
        self.write_sigs = {}
        self.write_txs = {}
        self.fail_on = set(fail_on)
        self.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
        self.writes_set = False

    def set_writes(self, writes: dict, pinned: dict | None = None):
        """writes: {programdata address: [(slot, utc), ...]} (the Upgrade txs); pinned: {address: pinned deploy slot} (the old deploy, always listed last)."""
        pinned = pinned or {"PD_PUMP": 100, "PD_SWAP": 200}
        self.writes_set = True
        for addr, items in writes.items():
            sigs = []
            for slot, utc in items:
                sig = f"w{addr}{slot}"
                bt = c.utc_ms(utc) // 1000
                self.write_txs[sig] = {"slot": slot, "blockTime": bt, "transaction": {"message": {"accountKeys": ["Auth", addr, c.LOADER_PROGRAM],
                                       "instructions": [{"programIdIndex": 2, "data": mon.b58encode(struct.pack("<I", c.LOADER_UPGRADE_TAG))}]}}, "meta": {"err": None}}
                sigs.append({"signature": sig, "slot": slot, "blockTime": bt, "err": None})
            sigs.append({"signature": f"pinned{addr}", "slot": pinned.get(addr, 1), "blockTime": 1, "err": None})
            self.write_sigs[addr] = sorted(sigs, key=lambda x: -x["slot"])

    def set_writes_from_rec(self, rec: dict):
        self.set_writes({rec["programs"][n]["programdata"]: [(rec["programs"][n]["deploy_slot"], rec["programs"][n]["deploy_utc"])] for n in ("pump", "pumpswap")
                         if rec["programs"][n].get("programdata") and rec["programs"][n]["deploy_slot"] != MON_PINS["programs"][n]["deploy_slot"]})

    def call(self, method, params):
        self.calls += 1
        if self.fail:
            raise self.fail
        if method in self.fail_on:
            raise AssertionError(f"{method} must not be called")
        if method == "getBlock":
            txs = []
            for i in range(self.per_block):
                blob, pid = self.blobs[i % len(self.blobs)]
                txs.append({"meta": {"err": None, "logMessages": logs_for(pid, blob)}})
            return {"transactions": txs}
        if method == "getSignaturesForAddress":
            if params[0] in self.write_sigs:
                return self.write_sigs[params[0]]
            if params[0] == mon.MIGRATION_FEE_ACCOUNT:
                return self.sigs
            return [{"signature": f"sl{j}", "slot": MIN_SLOT + 1000 + j, "blockTime": (NOW // 1000) - 7000 + j, "err": None} for j in range(self.n_slices)]
        if method == "getTransaction":
            return self.write_txs.get(params[0]) or self.txs.get(params[0])
        if method == "getMultipleAccounts":
            if "dataSlice" in params[1]:
                return {"value": [{"lamports": 0} for _ in params[0]]}
            data = bytearray(300)
            data[mon.POOL_MAYHEM_OFFSET] = 1 if self.mayhem else 0
            if self.pool_quote is not None:  # a PumpSwap Pool account: discriminator, quote mint at 75
                data[:8] = td._POOL_ACCOUNT_DISC
                data[75:107] = self.pool_quote
            return {"value": [{"data": [base64.b64encode(bytes(data)).decode(), "base64"]} for _ in params[0]]}
        raise AssertionError(method)


def http_ok(overrides=None):
    def get(url, timeout):
        name = url.rsplit("/", 1)[1][:-5]
        body = (overrides or {}).get(name, IDL_BYTES[name])
        return 200, {}, body
    return get


FROZEN_BLOBS = {rel: c.git_blob_sha(c.REPO_ROOT / rel) for rel in c.FROZEN_FILES}


def build_pins(blobs=None):
    blobs = blobs or sample_blobs()
    lens = {}
    for b, _ in blobs:
        h = b[:8].hex()
        lens[h] = {"min_len": len(b) - 5, "max_len": len(b) + 5}
    return {"idl": {k: {"sha256": hashlib.sha256(v).hexdigest()} for k, v in IDL_BYTES.items()}, "disc_len": lens, "ignored_discs": {}, "max_capped_share": 0.05,
            "trailing_bytes": {"TradeEvent": [8], "BuyEvent": [8], "SellEvent": [8], "WithdrawEvent": [8], "CreatePoolEvent": [0]},
            "boost": {"init_boost_share": 0.9, "min_init_boost_in_migrate_sample": 5}, "frozen_sha": "f" * 40, "frozen_blobs": dict(FROZEN_BLOBS),
            "migrate": {"required_events": ["CompleteEvent", "CompletePumpAmmMigrationEvent", "CreatePoolEvent", "InitBoostEvent"]}}


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
        "programs": {"pump": {"deploy_slot": 100, "deploy_utc": "2026-10-13T15:40:41Z", "programdata": "PD_PUMP"}, "pumpswap": {"deploy_slot": 200, "deploy_utc": "2026-10-08T16:20:03Z", "programdata": "PD_SWAP"},
                     "fees": {"deploy_slot": 300, "deploy_utc": "2026-10-12T17:40:24Z"}},
        "graduations": {"boost_denominator": {"n_nonmayhem_wsol": 20, "n_init_boost": 18}, "synthetic": {"n_checked": 20, "n_synthetic": 3, "share": 0.15}},
        "boost": {"budget_sol": {"median": 17.6}, "slices": {"median": 29}},
    }
    if redeploy:
        rec["programs"]["pump"] = {"deploy_slot": 1000, "deploy_utc": DEPLOY_PUMP, "programdata": "PD_PUMP"}
        rec["programs"]["pumpswap"] = {"deploy_slot": 1100, "deploy_utc": DEPLOY_SWAP, "programdata": "PD_SWAP"}
        flags["pins_changed"] = flag(True, reason="program_pump deploy_slot 100 -> 1000; program_pumpswap deploy_slot 200 -> 1100")
    for k, v in over.items():
        rec[k] = v
    return rec


NOW = ms("2026-10-20T07:20:00Z")


def run(rec, tmp_path, *, prev=None, pins=None, client=None, http=None, now=None, state=None, history=()):
    clock = now or (lambda: NOW)
    cl = client if client is not None else FakeClient(sample_blobs())
    if hasattr(cl, "writes_set") and not cl.writes_set and not cl.fail:
        cl.set_writes_from_rec(rec)
    client = cl
    return c.decide(rec, prev, MON_PINS, pins if pins is not None else build_pins(), state or tmp_path / "state.jsonl",
                    client=client if client is not None else FakeClient(sample_blobs()), http_get=http or http_ok(), now_ms=clock, job_id="job-1", history=history)


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
    assert v["detail"]["log_sample"]["n_txs"] == c.SAMPLE_MIN_TXS
    assert v["detail"]["migrate_sample"]["n_migrate_txs"] == c.MIGRATE_K and v["detail"]["migrate_sample"]["slot_min"] >= v["detail"]["sample_start_slot"]
    assert v["detail"]["migrate_sample"]["n_init_boost"] == c.MIGRATE_K and v["detail"]["migrate_sample"]["init_boost_share"] == 1.0
    assert v["redeploy_utc"] == DEPLOY_PUMP and v["deadline_utc"] == "2026-10-20T17:47:02Z"
    st = c.read_state(tmp_path / "state.jsonl")
    assert set(st) == {"pump:1000", "pumpswap:1100"} and st["pump:1000"]["job_id"] == "job-1" and st["pump:1000"]["verdict"] == "PASS"


def test_passed_slot_is_covered_not_reclassified(tmp_path):
    run(make_rec(), tmp_path)
    boom = FakeClient([], fail_on={"getBlock", "getMultipleAccounts"})  # only the write enumeration (signature list) may run
    v = run(make_rec(run_utc="2026-10-21T07:11:27Z"), tmp_path, client=boom, http=lambda *a: (_ for _ in ()).throw(AssertionError("no http")))
    assert v["verdict"] == "PASS" and v.get("covered") and v["halt_at_ms"] is None


def test_new_slot_after_a_pass_is_checked_again(tmp_path):
    run(make_rec(), tmp_path)
    rec = make_rec(run_utc="2026-10-22T07:11:27Z")
    rec["programs"]["pump"] = {"deploy_slot": 5000, "deploy_utc": "2026-10-22T01:00:00Z", "programdata": "PD_PUMP"}
    cl = FakeClient(sample_blobs(), slot0=5150, old_sig_slot=5000)
    cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP), (5000, "2026-10-22T01:00:00Z")], "PD_SWAP": [(1100, DEPLOY_SWAP)]})  # the monitor pins are not re-pinned: both pump writes are listed
    v = run(rec, tmp_path, client=cl, now=lambda: ms("2026-10-22T07:20:00Z"))
    assert v["verdict"] == "PASS" and not v.get("covered") and v["per_write"] == {"pump:5000": "PASS"}


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
    del rec["halt"]["flags"]["boost_disabled"]
    return "a: flag boost_disabled absent"


@_case("e_share_off")
def _e1(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs(), boost_every=2)  # 12 of 24 post-deploy graduations carry InitBoost: 0.5 vs 0.9
    return "e: InitBoost share"


@_case("e_budget_off")
def _e2(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs(), budget_sol=22.0)
    return "e: BOOST budget median"


@_case("e_slices_off")
def _e3(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs(), n_slices=40)
    return "e: BOOST slices median"


@_case("e_few_init_boost_events")
def _e4(rec, pins, kw):
    pins["boost"]["min_init_boost_in_migrate_sample"] = 10_000
    return "e: InitBoost events in the migrate sample"


@_case("d_migrate_required_event_missing")
def _e5(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs(), migrate_drop=(td._CREATE_POOL_DISC,))
    return "d: CreatePoolEvent not seen in the migrate sample"


@_case("c_migrate_unknown_discriminator")
def _e6(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs(), migrate_extra=(b"\x09" * 8 + b"\x00" * 40,))
    return "c: migrate sample: unknown discriminators"


@_case("d_migrate_trailing_bytes_unpinned")
def _e7(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs(), migrate_extra=(encode("InitBoostEvent", prog="pump_amm") + b"\x00" * 8,))
    return "d: migrate sample: event bytes not consumed exactly"


@_case("f_idl_changed")
def _f(rec, pins, kw):
    changed = json.loads(IDL_BYTES["pump_amm"])
    changed["metadata"]["version"] = "9"
    kw["http"] = http_ok({"pump_amm": json.dumps(changed).encode()})
    return "f: idl pump_amm sha256"


@_case("c_unknown_discriminator")
def _c1(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs([(b"\x01\x02\x03\x04\x05\x06\x07\x08" + b"\x00" * 40, SWAP)]))
    return "c: log sample: unknown discriminators"


@_case("c_undecodable_known_event")
def _c2(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs([(encode("SellEvent", prog="pump_amm", trunc=100), SWAP)]))
    return "c: log sample: undecodable events"


@_case("c_shadow_differs_from_idl")
def _c3(rec, pins, kw):
    real = c.shadow_decode

    def bad(raw):
        row = real(raw)
        if row is not None and row.get("kind") == "trade" and row["side"] == "buy" and row["venue"] == "pumpswap":
            row = dict(row, sol_lamports=row["sol_lamports"] + 1)
        return row
    kw["patch"] = ("shadow_decode", bad)
    return "c: log sample: shadow decode differs from the IDL decode"


@_case("c_sample_short")
def _c4(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs(), per_block=1)  # 300 slots x 1 tx
    return "c: sample has 300 txs"


@_case("d_length_outside_pin")
def _d1(rec, pins, kw):
    h = encode("BuyEvent", prog="pump_amm")[:8].hex()
    pins["disc_len"][h] = {"min_len": 10, "max_len": 20}
    return "d: log sample: discriminator"


@_case("d_required_event_missing")
def _d2(rec, pins, kw):
    kw["client"] = FakeClient([b for b in sample_blobs() if b[0][:8] != td._SELL_DISC])
    return "d: SellEvent not seen"


@_case("d_layout_not_consumed_exactly")
def _d3(rec, pins, kw):
    kw["client"] = FakeClient(sample_blobs([(encode("SellEvent", prog="pump_amm") + b"\x00" * 8, SWAP)]))  # a field appended after the IDL's last
    pins["disc_len"][encode("SellEvent", prog="pump_amm")[:8].hex()] = {"min_len": 400, "max_len": 500}
    return "d: log sample: event bytes not consumed exactly"


@_case("c_capped_share_too_high")
def _c5(rec, pins, kw):
    pins["max_capped_share"] = 0.0
    kw["client"] = FakeClient(sample_blobs([(huge_swap("BuyEvent"), SWAP)]))
    return "c: log sample: 80/250 Buy/Sell events above the shadow"


def huge_swap(name):
    raw = bytearray(encode(name, prog="pump_amm"))
    fld = {"BuyEvent": 112, "SellEvent": 112}[name]
    raw[fld:fld + 8] = struct.pack("<Q", 700_000 * 10**9)  # user_quote_amount_* above the shadow's 100,000 SOL cap
    return bytes(raw)


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
    rec["programs"]["pumpswap"] = {"deploy_slot": 1100, "deploy_utc": "2026-10-19T17:40:00Z", "programdata": "PD_SWAP"}
    rec["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)
    v = run(rec, tmp_path)
    assert v["verdict"] == "FAIL" and v["halt_at_ms"] == ms("2026-10-19T17:40:00Z")


def test_missing_pin_is_error_and_halts_at_redeploy(tmp_path):
    pins = build_pins()
    pins["idl"]["pump"]["sha256"] = None
    v = run(make_rec(), tmp_path, pins=pins)
    assert v["verdict"] == "ERROR" and "pin missing" in v["reasons"][0] and v["halt_at_ms"] == ms(DEPLOY_PUMP)


def test_shipped_pins_are_complete_and_a_null_anywhere_is_error(tmp_path):
    shipped = json.loads((Path(c.__file__).with_name("a3_redeploy_pins.json")).read_text())

    def nulls(o):
        return o is None or (isinstance(o, dict) and any(nulls(v) for v in o.values()))
    assert not nulls(shipped) and shipped["trailing_bytes"]["WithdrawEvent"] == [8]
    assert len(shipped["idl"]) == 3 and all(len(v["sha256"]) == 64 for v in shipped["idl"].values())
    for key in ("max_capped_share", "boost"):
        broken = copy.deepcopy(shipped)
        broken[key] = None if key == "max_capped_share" else {"init_boost_share": None, "min_init_boost_in_sample": 0}
        assert run(make_rec(), tmp_path / key, pins=broken | {"idl": build_pins()["idl"]})["verdict"] == "ERROR"


def test_trailing_bytes_pin_allows_exactly_the_pinned_count():
    raw = encode("SellEvent", prog="pump_amm")  # carries 8 trailing bytes
    assert c.evaluate_sample([logs_for(SWAP, raw)], IDLS, None, {"SellEvent": [8]})["layout_mismatch"] == {}
    assert c.evaluate_sample([logs_for(SWAP, raw)], IDLS, None, {"SellEvent": [0]})["layout_mismatch"] == {"SellEvent": 1}
    assert c.evaluate_sample([logs_for(SWAP, raw[:-3])], IDLS, None, {"SellEvent": [8]})["layout_mismatch"] == {"SellEvent": 1}


def test_event_without_idl_entry_and_without_length_pin_is_error(tmp_path):
    pins = build_pins()
    h = encode("CreatePoolEvent", prog="pump_amm")[:8].hex()
    del pins["disc_len"][h]
    assert run(make_rec(), tmp_path, pins=pins)["verdict"] == "PASS"  # IDL consumes it exactly: no length pin needed
    idl = copy.deepcopy(IDLS)
    idl["pump_amm"]["events"] = [e for e in idl["pump_amm"]["events"] if e["name"] != "CreatePoolEvent"]
    body = {**IDL_BYTES, "pump_amm": json.dumps(idl["pump_amm"], sort_keys=True).encode()}
    pins["idl"]["pump_amm"]["sha256"] = hashlib.sha256(body["pump_amm"]).hexdigest()
    v = run(make_rec(), tmp_path / "y", pins=pins, http=http_ok(body))
    assert v["verdict"] == "ERROR" and "disc_len" in v["reasons"][0]


def test_idl_fetch_failure_is_error(tmp_path):
    v = run(make_rec(), tmp_path, http=lambda url, t: (503, {}, b""))
    assert v["verdict"] == "ERROR" and v["halt_at_ms"] == ms(DEPLOY_PUMP)


@pytest.mark.parametrize("exc", [mon.RpcError("boom"), mon.RpcUnreachable("down"), mon.CallBudgetExceeded("cap")])
def test_rpc_failure_is_error(tmp_path, exc):
    v = run(make_rec(), tmp_path, client=FakeClient([], fail=exc))
    # nothing could be enumerated: fail closed at the pinned deploys' own instants (the earliest, PumpSwap 2026-10-08), not at the record's newest write
    assert v["verdict"] == "ERROR" and v["halt_at_ms"] == ms(PIN_SWAP_UTC) < ms(DEPLOY_PUMP)


def test_unmapped_idl_layout_is_error_not_pass(tmp_path):
    idl = json.loads(IDL_BYTES["pump_amm"])
    for t in idl["types"]:
        if t["name"] == "BuyEvent":
            t["type"]["fields"] = [f for f in t["type"]["fields"] if f["name"] != "coin_creator_fee"]
    pins = build_pins()
    pins["idl"]["pump_amm"]["sha256"] = hashlib.sha256(json.dumps(idl).encode()).hexdigest()
    v = run(make_rec(), tmp_path, pins=pins, http=http_ok({"pump_amm": json.dumps(idl).encode()}))
    assert v["verdict"] == "FAIL" and any("c: log sample: shadow decode differs" in r for r in v["reasons"])


def test_timeout_when_started_late(tmp_path):
    late = ms("2026-10-20T17:47:03Z")  # 24 h and 1 s after the pump redeploy
    cl = FakeClient([], fail_on={"getBlock", "getMultipleAccounts"})
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
    cl = FakeClient([], fail_on={"getBlock", "getMultipleAccounts"})  # the write enumeration may run; the check (samples) must not
    return run(rec, tmp_path, prev=prev, client=cl, http=lambda *a: (_ for _ in ()).throw(AssertionError("no http")))


@pytest.mark.parametrize("name", list(c.BOOST_FLAGS))
def test_boost_flags_hard_halt_at_flag_instant(name, tmp_path):
    rec = make_rec()
    rec["halt"]["flags"][name] = flag(True, reason="x")
    v = hard(rec, tmp_path)
    # B1: a hard flag in the same run as a not-yet-PASSed program redeploy ends the window at the REDEPLOY instant (earlier than the flag's)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(DEPLOY_PUMP) < ms(RUN_UTC) and v["reasons"][0].startswith(name) and v["flag_halt_ms"] == ms(RUN_UTC)


# ---- synthetic_share_high: pooled z-test over the newest K non-overlapping runs (QP-pinned constants)
def syn_rec(run_utc, n_syn, n_chk, single_flag=None, slots=None, **kw):
    rec = make_rec(redeploy=False, run_utc=run_utc, **kw)
    rec["graduations"]["synthetic"] = {"n_checked": n_chk, "n_synthetic": n_syn, "share": n_syn / n_chk}
    if slots:
        rec["graduations"]["slot_min"], rec["graduations"]["slot_max"] = slots
    if single_flag is not None:
        rec["halt"]["flags"]["synthetic_share_high"] = flag(single_flag, reason="single run")
    return rec


def syn_series(counts, tail="2026-10-22T19:10:00Z"):
    """counts oldest first, the last is the current run at `tail`; runs 12 h apart with disjoint slot ranges. The raw single-run flag is set from the share, as the monitor does."""
    from datetime import datetime, timedelta
    end = datetime.strptime(tail, "%Y-%m-%dT%H:%M:%SZ")
    n = len(counts)
    return [syn_rec((end - timedelta(hours=12 * (n - 1 - i))).strftime("%Y-%m-%dT%H:%M:%SZ"), x, k, single_flag=x / k > 0.35, slots=(1000 * i, 1000 * i + 500)) for i, (x, k) in enumerate(counts)]


def pooled(recs):
    return c.pooled_synthetic_flag(recs[-1], recs[:-1])


def test_pooled_synthetic_constants():
    assert c.SYN_P0 == 75 / 339 and c.SYN_PHI == 2.0 and c.SYN_Z_HALT == 3.0902 and c.SYN_POOL_K == 4 and c.SYN_MIN_RUN_CHECKED == 10


def test_1022_case_single_run_spike_does_not_halt():
    recs = syn_series([(3, 20), (0, 20), (7, 20), (8, 19)])
    assert recs[-1]["halt"]["flags"]["synthetic_share_high"]["halt"] is True  # the monitor's single-run flag fires
    t = c.triage(recs[-1], MON_PINS, None, recs[:-1])
    assert t["action"] == "none" and t["flag_halt_ms"] is None and not t["hard_reasons"]
    f = pooled(recs)
    assert f["evaluated"] and not f["halt"] and "single-run WARN 8/19 = 0.421" in f["reason"]


def test_sustained_synthetic_regime_halts():
    recs = syn_series([(9, 20)] * 4)
    f = pooled(recs)
    assert f["halt"] and f["z"] > 3.0902
    t = c.triage(recs[-1], MON_PINS, None, recs[:-1])
    assert t["action"] == "hard" and t["flag_halt_ms"] == ms(recs[-1]["run_utc"]) and t["hard_reasons"][0].startswith("synthetic_share_high")


def test_z_matches_ruling_formula_and_boundary():
    assert abs(c.synthetic_z(36, 80) - (36 - 80 * 75 / 339) / (2.0 * 80 * (75 / 339) * (264 / 339)) ** 0.5) < 1e-12
    assert not pooled(syn_series([(8, 20)] * 4))["halt"]  # 32/80, z ~2.6
    assert pooled(syn_series([(9, 20)] * 4))["halt"]  # 36/80, z ~3.49


def test_pool_only_newest_k():
    recs = syn_series([(20, 20), (20, 20), (1, 20), (1, 20), (1, 20), (1, 20)])
    f = pooled(recs)
    assert not f["halt"] and "4/80" in f["reason"]


def test_pool_ignores_later_records_and_same_run_utc():
    recs = syn_series([(1, 20)] * 4)
    later = syn_rec("2026-10-23T07:10:00Z", 20, 20, slots=(90000, 90500))
    cur = recs[2]
    f = c.pooled_synthetic_flag(cur, recs[:2] + [recs[3], later])
    assert not f["halt"] and "3/60" in f["reason"]
    assert "3/60" in c.pooled_synthetic_flag(cur, recs[:3])["reason"]  # the record itself in the history is not counted twice


def test_overlapping_samples_counted_once():
    a = syn_rec("2026-10-22T07:10:00Z", 9, 20, slots=(100, 200))
    b = syn_rec("2026-10-22T07:12:00Z", 9, 20, slots=(150, 260))  # minutes later, overlaps a
    cur = syn_rec("2026-10-22T19:10:00Z", 9, 20, slots=(500, 600))
    f = c.pooled_synthetic_flag(cur, [a, b])
    assert "18/40" in f["reason"] and "2 non-overlapping" in f["reason"]  # b (newer) kept, a skipped
    far = syn_rec("2026-10-21T19:10:00Z", 9, 20, slots=(10, 20))
    assert "27/60" in c.pooled_synthetic_flag(cur, [far, a, b])["reason"]


def test_newest_record_without_valid_counts_is_unevaluated():
    for bad in (None, {"n_checked": 9, "n_synthetic": 9}, {"n_checked": "20", "n_synthetic": 1}, {"n_checked": 20, "n_synthetic": 21}):
        recs = syn_series([(9, 20)] * 3)
        cur = make_rec(redeploy=False, run_utc="2026-10-22T19:10:00Z")
        if bad is None:
            del cur["graduations"]["synthetic"]
        else:
            cur["graduations"]["synthetic"] = bad
        f = c.pooled_synthetic_flag(cur, recs)
        assert f["evaluated"] is False and f["halt"] is False  # older records never stand in for a missing newest one


def test_two_consecutive_unevaluated_synthetic_hard_halts():
    prev = make_rec(redeploy=False, run_utc="2026-10-21T19:10:00Z")
    del prev["graduations"]["synthetic"]
    cur = make_rec(redeploy=False)
    del cur["graduations"]["synthetic"]
    t = c.triage(cur, MON_PINS, prev, [prev])
    assert t["action"] == "hard" and any("synthetic_share_high: unevaluated on two consecutive" in r for r in t["hard_reasons"])
    prev_ok = make_rec(redeploy=False, run_utc="2026-10-21T19:10:00Z")
    assert c.triage(cur, MON_PINS, prev_ok, [prev_ok])["action"] == "none"


def test_redeploy_check_a_uses_pooled_flag_not_raw(tmp_path):
    # C1: a redeploy run whose raw single-run flag is set but whose pool is clear must not FAIL (a)
    hist = syn_series([(3, 20), (0, 20), (7, 20)], tail="2026-10-22T07:10:00Z")
    rec = make_rec(run_utc="2026-10-22T19:10:00Z")
    rec["graduations"]["synthetic"] = {"n_checked": 19, "n_synthetic": 8, "share": 8 / 19}
    rec["halt"]["flags"]["synthetic_share_high"] = flag(True, reason="single run 8/19")
    v = run(rec, tmp_path, history=hist, prev=hist[-1])
    assert not any("a:" in r and "synthetic" in r for r in v["reasons"]), v
    assert v["verdict"] != "HALT" or not any(r.startswith("synthetic_share_high") for r in v["reasons"])
    # pooled over -> hard halt
    hist2 = syn_series([(9, 20)] * 3, tail="2026-10-22T07:10:00Z")
    rec2 = make_rec(run_utc="2026-10-22T19:10:00Z")
    rec2["graduations"]["synthetic"] = {"n_checked": 20, "n_synthetic": 9, "share": 0.45}
    rec2["halt"]["flags"]["synthetic_share_high"] = flag(False)  # raw clear; pooled decides
    v2 = run(rec2, tmp_path, history=hist2, prev=hist2[-1], state=tmp_path / "s2.jsonl")
    assert v2["verdict"] == "HALT" and any(r.startswith("synthetic_share_high") for r in v2["reasons"])


def test_boost_flag_alone_hard_halts(tmp_path):
    rec = make_rec(redeploy=False)
    rec["halt"]["flags"]["boost_disabled"] = flag(True)
    assert hard(rec, tmp_path)["halt_at_ms"] == ms(RUN_UTC)


@pytest.mark.parametrize("acct", ["bonding_fee_config", "pumpswap_fee_config", "pumpswap_global_config"])
def test_fee_or_global_config_change_hard_halts(acct, tmp_path):
    rec = make_rec()
    rec["accounts"][acct]["sha256"] = "f" * 64
    v = hard(rec, tmp_path)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(DEPLOY_PUMP)
    assert any(acct in r for r in v["reasons"])
    rec2 = make_rec(redeploy=False)
    rec2["accounts"][acct]["sha256"] = "f" * 64
    rec2["halt"]["flags"]["pins_changed"] = flag(True, reason="account")
    assert hard(rec2, tmp_path / "n")["halt_at_ms"] == ms(RUN_UTC)  # no redeploy: the flag's instant


def test_fees_program_redeploy_hard_halts_even_with_pump(tmp_path):
    rec = make_rec()
    rec["programs"]["fees"] = {"deploy_slot": 999, "deploy_utc": "2026-10-19T20:00:00Z"}
    v = hard(rec, tmp_path)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(DEPLOY_PUMP) and any("fees" in r for r in v["reasons"])


def test_b1_covered_redeploy_does_not_pull_a_hard_halt_earlier(tmp_path):
    run(make_rec(), tmp_path)  # PASS: both slots covered
    rec = make_rec(run_utc="2026-10-21T07:11:27Z")
    rec["halt"]["flags"]["boost_disabled"] = flag(True)
    v = hard(rec, tmp_path)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms("2026-10-21T07:11:27Z")


def test_b1_hard_flag_with_a_new_pending_slot_uses_the_earlier_pending_instant(tmp_path):
    run(make_rec(), tmp_path)
    rec = make_rec(run_utc="2026-10-22T07:11:27Z")
    rec["programs"]["pump"] = {"deploy_slot": 5000, "deploy_utc": "2026-10-22T01:00:00Z", "programdata": "PD_PUMP"}
    rec["halt"]["flags"]["boost_disabled"] = flag(True)
    assert hard(rec, tmp_path)["halt_at_ms"] == ms("2026-10-22T01:00:00Z")


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
    bad["programs"]["pump"] = {"deploy_slot": 7777, "deploy_utc": "2026-10-21T01:00:00Z", "programdata": "PD_PUMP"}
    bad["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)
    write_daily(tmp_path / "daily.jsonl", [make_rec(), bad])
    cl2 = FakeClient(sample_blobs())
    cl2.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP), (7777, "2026-10-21T01:00:00Z")], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    assert c.main(argv(tmp_path), client=cl2, http_get=http_ok(), now_ms=lambda: ms("2026-10-21T07:15:00Z")) == 10
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
    assert out["idl_sha256"]["pump"] == hashlib.sha256(IDL_BYTES["pump"]).hexdigest() and out["frozen_blobs"] == FROZEN_BLOBS
    assert out["migrate_sample"]["n_init_boost"] == c.MIGRATE_K and not out["migrate_sample"]["unknown"] and out["migrate_sample"]["trailing_seen"]["InitBoostEvent"] == [0]
    assert out["log_sample"]["n_txs"] == c.SAMPLE_MIN_TXS and out["migrate_sample"]["budget_sol_median"] == 17.585 and out["migrate_sample"]["slices_median"] == 29
    assert not (tmp_path / "st.jsonl").exists()


def test_swap_above_the_amount_cap_is_explained_not_undecodable(tmp_path):
    """The 10-14 review's unexplained '3 + 3 PumpSwap Buy/Sell not decoded': the shadow refuses a quote amount above 100,000 SOL. Counted apart, bounded by the pinned share."""
    pins = build_pins()
    pins["max_capped_share"] = 0.5
    blobs = sample_blobs([(huge_swap("BuyEvent"), SWAP), (huge_swap("SellEvent"), SWAP)])
    v = run(make_rec(), tmp_path, pins=pins, client=FakeClient(blobs, per_block=100))
    assert v["verdict"] == "PASS", v["reasons"]
    ls = v["detail"]["log_sample"]
    assert ls["capped"] > 0 and ls["capped"] / ls["n_swap_events"] < 0.5


def test_other_buy_sell_non_decode_is_still_undecodable():
    short = encode("SellEvent", prog="pump_amm", trunc=100)
    ev = c.evaluate_sample([logs_for(SWAP, short)], IDLS)
    assert ev["undecodable"] == {td._SELL_DISC.hex(): 1} and ev["capped"] == 0
    bad_ts = bytearray(encode("BuyEvent", prog="pump_amm"))
    bad_ts[8:16] = struct.pack("<q", 5)
    bad_ts[112:120] = struct.pack("<Q", 700_000 * 10**9)
    ev = c.evaluate_sample([logs_for(SWAP, bytes(bad_ts))], IDLS)
    assert ev["undecodable"] and ev["capped"] == 0  # an insane timestamp is not an explained cap


def test_pinned_ignored_discriminator_is_accepted_only_with_its_idl_name():
    other = bytes(hashlib.sha256(b"event:ExtendAccountEvent").digest()[:8]) + b"\x00" * 40
    log = logs_for(SWAP, other)
    assert c.evaluate_sample([log], IDLS)["unknown"] == {other[:8].hex(): 1}
    assert c.evaluate_sample([log], IDLS, {other[:8].hex(): "ExtendAccountEvent"})["unknown"] == {}
    # an IDL that names the discriminator differently than the pin is not accepted
    idl = copy.deepcopy(IDLS)
    idl["pump_amm"]["events"].append({"name": "OtherEvent", "discriminator": list(other[:8])})
    idl["pump_amm"]["types"].append({"name": "OtherEvent", "type": {"kind": "struct", "fields": []}})
    assert c.evaluate_sample([log], idl, {other[:8].hex(): "ExtendAccountEvent"})["unknown"] == {other[:8].hex(): 1}


def test_previous_daily_skips_same_day_manual_runs():
    recs = [{"run_utc": "2026-10-19T07:00:00Z", "n": 1}, {"run_utc": "2026-10-20T07:00:00Z", "n": 2}, {"run_utc": "2026-10-21T07:00:00Z", "n": 3},
            {"run_utc": "2026-10-21T09:00:00Z", "n": 4}, {"run_utc": "2026-10-21T17:55:29Z", "n": 5}]
    assert c.previous_daily(recs)["n"] == 2  # the previous UTC date's newest run, not the 09:00 manual run
    assert c.previous_daily(recs[:1]) is None
    assert c.previous_daily([{"run_utc": "2026-10-21T07:00:00Z"}, {"run_utc": "2026-10-21T09:00:00Z"}]) is None


def test_unexpected_exception_in_the_check_is_error_and_recorded(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise ZeroDivisionError("pin of zero")
    monkeypatch.setattr(c, "check_redeploy", boom)
    v = run(make_rec(), tmp_path)
    assert v["verdict"] == "ERROR" and "ZeroDivisionError" in v["reasons"][0] and v["halt_at_ms"] == ms(DEPLOY_PUMP)
    assert c.read_state(tmp_path / "state.jsonl")["pump:1000"]["verdict"] == "ERROR"


def test_short_blobs_are_counted_as_undecodable():
    ev = c.evaluate_blobs([b"\x01\x02", encode("TradeEvent", prog="pump")], IDLS)
    assert ev["undecodable"] == {"short": 1} and ev["n_events"] == 2


def test_state_write_failure_is_operational_error_not_silent(tmp_path, capsys):
    write_pins_files(tmp_path, build_pins())
    write_daily(tmp_path / "daily.jsonl", [make_rec()])
    (tmp_path / "st.jsonl").mkdir()  # cannot be appended to
    rc = c.main(argv(tmp_path), client=FakeClient(sample_blobs()), http_get=http_ok(), now_ms=lambda: NOW)
    assert rc == 11 and not (tmp_path / "v.json").exists()


# ---- C-3, C-2 and the migrate sample
def test_frozen_guard_error_on_a_changed_decoder_file(tmp_path):
    pins = build_pins()
    pins["frozen_blobs"]["observe/trade_decode.py"] = "0" * 40
    v = run(make_rec(), tmp_path, pins=pins)
    assert v["verdict"] == "ERROR" and "decoder files differ" in v["reasons"][0] and v["halt_at_ms"] == ms(DEPLOY_PUMP)
    pins = build_pins()
    del pins["frozen_blobs"]
    assert run(make_rec(), tmp_path / "x", pins=pins)["verdict"] == "ERROR"


def test_frozen_guard_matches_the_shipped_pins_at_head():
    """The pins are the git blobs at the covered book's frozen_sha, and HEAD still matches them, with ONE known and covered exception: tools/h5_shadow.py.
    #789 (a5f41370, 2026-10-21 17:17 PT, merged after the #778 pin) added --q-star-sol to h5_shadow.py, so its HEAD blob (f23cf49221) differs from the pinned a2 frozen blob
    (23b9ccc256). The pin stays: QP ruling 2026-10-22 G6 (ARTIFACTS/lab/qp-ruling-1022-q50.md:129) says a2 runs at 1f7a89c and pins must not be edited while it runs; the
    decode-path source identity of h5_shadow.py between 1f7a89c and HEAD is proved by tools/test_h5_shadow_qstar_identity.py. The old form of this test (frozen_guard(shipped)
    at HEAD, no exception) has failed since #789. A re-pin by a QP-OK PR makes the drift set empty and this test still passes."""
    root = Path(c.__file__).resolve().parent.parent
    shipped = json.loads((Path(c.__file__).with_name("a3_redeploy_pins.json")).read_text())
    blobs = shipped["frozen_blobs"]
    assert set(blobs) == set(c.FROZEN_FILES)
    for rel, want in sorted(blobs.items()):  # the pin file itself is right: blob at frozen_sha == pin (fail, never skip, if the object is missing)
        r = subprocess.run(["git", "-C", str(root), "rev-parse", f"{shipped['frozen_sha']}:{rel}"], capture_output=True, text=True)
        if r.returncode != 0:
            pytest.fail(f"object {shipped['frozen_sha']} is not in this clone: git fetch it")
        assert r.stdout.strip() == want, rel
    drift = {rel for rel, want in blobs.items() if c.git_blob_sha(root / rel) != want}
    assert drift <= {"tools/h5_shadow.py"}, sorted(drift)


def test_too_few_post_deploy_migrate_txs_is_error(tmp_path):
    v = run(make_rec(), tmp_path, client=FakeClient(sample_blobs(), n_migrate=c.MIGRATE_K_MIN - 1))
    assert v["verdict"] == "ERROR" and "migrate txs" in v["reasons"][0]


def test_boost_evidence_needs_min_eval_n_graduations(tmp_path):
    v = run(make_rec(), tmp_path, client=FakeClient(sample_blobs(), mayhem=True))  # every post-deploy pool is mayhem: no denominator
    assert v["verdict"] == "ERROR" and "BOOST evidence" in v["reasons"][0]


def test_boost_evidence_ignores_pre_deploy_graduations(tmp_path):
    cl = FakeClient(sample_blobs(), old_sig_slot=1249)  # a migrate sig just below the start slot is the 'older than start' marker and is never fetched
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "PASS" and v["detail"]["migrate_sample"]["slot_min"] >= 1250
    assert "old" not in cl.txs


def test_start_slot_not_reached_is_error(tmp_path):
    cl = FakeClient(sample_blobs())
    cl.sigs = [x for x in cl.sigs if x["signature"] != "old"]  # fewer than 1000 sigs and none older than the start slot => the list ended: that IS 'reached'
    assert run(make_rec(), tmp_path, client=cl)["verdict"] == "PASS"
    big = [{"signature": f"s{i}", "slot": 10_000 + i, "blockTime": 1, "err": None} for i in range(1000)]
    cl2 = FakeClient(sample_blobs())
    cl2.sigs = big
    cl2.call_orig = cl2.call
    v = run(make_rec(), tmp_path / "y", client=cl2)
    assert v["verdict"] == "ERROR" and "not reached" in v["reasons"][0]


def test_migrate_events_through_the_frozen_path_equal_the_idl_decode():
    tx = migrate_tx(3)
    blobs = mon.tx_event_blobs(tx)
    ev = c.evaluate_blobs(blobs, IDLS)
    assert ev["idl_mismatch"] == {} and ev["unknown"] == {} and ev["undecodable"] == {} and ev["layout_mismatch"] == {}
    assert set(ev["seen_events"]) == {"CompleteEvent", "CompletePumpAmmMigrationEvent", "CreatePoolEvent", "InitBoostEvent"}


# ---- QP repin ruling C3'b / C3'c
def test_shipped_trailing_pin_is_exactly_8_on_four_events_and_0_elsewhere():
    shipped = json.loads((Path(c.__file__).with_name("a3_redeploy_pins.json")).read_text())
    tb = c.validate_trailing_pins(shipped["trailing_bytes"])
    assert {k for k, v in tb.items() if v == [8]} == {"TradeEvent", "BuyEvent", "SellEvent", "WithdrawEvent"}
    assert all(v == [0] for k, v in tb.items() if k not in c.TRAILING_EVENTS)


@pytest.mark.parametrize("bad", [{"TradeEvent": [0, 8], "BuyEvent": [8], "SellEvent": [8], "WithdrawEvent": [8]},
                                 {"TradeEvent": [8], "BuyEvent": [8], "SellEvent": [8]},
                                 {"TradeEvent": [8], "BuyEvent": [8], "SellEvent": [8], "WithdrawEvent": [8], "CreatePoolEvent": [8]},
                                 {"TradeEvent": [4], "BuyEvent": [8], "SellEvent": [8], "WithdrawEvent": [8]},
                                 {"TradeEvent": 8, "BuyEvent": [8], "SellEvent": [8], "WithdrawEvent": [8]}, None, {}])
def test_loose_trailing_pins_are_error(bad, tmp_path):
    with pytest.raises(c.CheckError):
        c.validate_trailing_pins(bad)
    pins = build_pins()
    pins["trailing_bytes"] = bad
    assert run(make_rec(), tmp_path, pins=pins)["verdict"] == "ERROR"


def test_any_other_trailing_count_is_a_hard_fail(tmp_path):
    for trail in (b"", b"\x00" * 16):  # 0 and 16 where exactly 8 is pinned
        v = run(make_rec(), tmp_path / str(len(trail)), client=FakeClient(sample_blobs([(encode("SellEvent", prog="pump_amm", trail=trail), SWAP)])))
        assert v["verdict"] == "FAIL" and any("not consumed exactly" in r for r in v["reasons"])
    v = run(make_rec(), tmp_path / "other", client=FakeClient(sample_blobs([(encode("CreatePoolEvent", prog="pump_amm") + b"\x00" * 8, SWAP)])))
    assert v["verdict"] == "FAIL"


NONZERO = struct.pack("<Q", 12345)


def test_nonzero_added_field_on_a_wsol_pumpswap_trade_is_a_hard_halt(tmp_path):
    blob = encode("BuyEvent", prog="pump_amm", over={"pool": mon.b58decode(POOLS[0])}, trail=NONZERO)
    v = run(make_rec(), tmp_path, client=FakeClient(sample_blobs([(blob, SWAP)])))  # the fake Pool account's quote mint is WSOL
    assert v["verdict"] == "FAIL" and any("HARD" in r and "NON-ZERO" in r for r in v["reasons"]) and v["halt_at_ms"] == ms(DEPLOY_PUMP)


def test_nonzero_added_field_on_a_non_sol_quote_is_allowed_and_counted(tmp_path):
    blob = encode("BuyEvent", prog="pump_amm", over={"pool": mon.b58decode(POOLS[0])}, trail=NONZERO)
    trade = encode("TradeEvent", prog="pump", over={"quote_mint": bytes([77]) * 32}, trail=NONZERO)
    v = run(make_rec(), tmp_path, client=FakeClient(sample_blobs([(blob, SWAP), (trade, PUMP)]), pool_quote=bytes([77]) * 32))
    assert v["verdict"] == "PASS", v["reasons"]


def test_nonzero_added_field_on_a_sol_quoted_bonding_trade_is_a_hard_halt(tmp_path):
    for q in (bytes(32), mon.b58decode(mon.WSOL_MINT)):
        trade = encode("TradeEvent", prog="pump", over={"quote_mint": q}, trail=NONZERO)
        v = run(make_rec(), tmp_path / q.hex()[:4], client=FakeClient(sample_blobs([(trade, PUMP)])))
        assert v["verdict"] == "FAIL" and any("NON-ZERO" in r for r in v["reasons"])


def test_nonzero_on_an_unreadable_pool_fails_closed(tmp_path):
    blob = encode("SellEvent", prog="pump_amm", over={"pool": mon.b58decode(POOLS[0])}, trail=NONZERO)
    v = run(make_rec(), tmp_path, client=FakeClient(sample_blobs([(blob, SWAP)]), pool_quote=None))
    assert v["verdict"] == "FAIL" and any("NON-ZERO" in r for r in v["reasons"])


# ---- QP repin ruling Q4: every programdata write is its own redeploy
EARLY_SWAP = "2026-10-19T15:46:16Z"  # an extra, earlier PumpSwap write on the same day (the 10-19 case)


def multi_client(**kw):
    cl = FakeClient(sample_blobs(), **kw)
    cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(900, EARLY_SWAP), (1100, DEPLOY_SWAP)]})
    return cl


def test_every_write_is_enumerated_with_its_own_state_and_check(tmp_path):
    rec = make_rec()
    v = run(rec, tmp_path, client=multi_client())
    assert v["verdict"] == "PASS", v["reasons"]
    assert sorted(v["per_write"]) == ["pump:1000", "pumpswap:1100", "pumpswap:900"] and set(v["per_write"].values()) == {"PASS"}
    assert set(c.read_state(tmp_path / "state.jsonl")) == {"pump:1000", "pumpswap:1100", "pumpswap:900"}
    assert [r["slot"] for r in v["redeploys"]] == [900, 1000, 1100]


def test_fail_instant_is_the_earliest_unreviewed_write_not_the_newest(tmp_path):
    rec = make_rec()
    pins = build_pins()
    v = run(rec, tmp_path, pins=pins, client=multi_client(pool_quote=None), http=http_ok())  # all clean...
    assert v["verdict"] == "PASS"
    # ...now an unreviewed earlier write with a failing check: the BOOST evidence is bad for every write, so the FIRST one (15:46Z) fails and ends the window
    rec2 = make_rec()
    rec2["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)
    v2 = run(rec2, tmp_path / "b", client=multi_client())
    assert v2["verdict"] == "FAIL" and v2["halt_at_ms"] == ms(EARLY_SWAP) and v2["reasons"][0].startswith("pumpswap:900")
    st = c.read_state(tmp_path / "b" / "state.jsonl")
    assert st["pumpswap:900"]["verdict"] == "FAIL" and "not evaluated" in st["pump:1000"]["reasons"][0] and st["pumpswap:1100"]["verdict"] == "FAIL"


def test_a_reviewed_earlier_write_does_not_set_the_instant_of_a_later_failure(tmp_path):
    cl = FakeClient(sample_blobs())
    cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    assert run(make_rec(), tmp_path, client=cl)["verdict"] == "PASS"
    rec = make_rec(run_utc="2026-10-22T07:11:27Z")
    rec["programs"]["pump"] = {"deploy_slot": 5000, "deploy_utc": "2026-10-22T01:00:00Z", "programdata": "PD_PUMP"}
    rec["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)
    cl2 = FakeClient(sample_blobs(), slot0=5150, old_sig_slot=5000)
    cl2.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP), (5000, "2026-10-22T01:00:00Z")], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    v = run(rec, tmp_path, client=cl2, now=lambda: ms("2026-10-22T07:20:00Z"))
    assert v["verdict"] == "FAIL" and v["halt_at_ms"] == ms("2026-10-22T01:00:00Z")


def test_pinned_slot_not_reached_fails_closed(tmp_path):
    cl = FakeClient(sample_blobs())
    cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    for addr in cl.write_sigs:
        cl.write_sigs[addr] = [x for x in cl.write_sigs[addr] if not x["signature"].startswith("pinned")]  # the list ends without the pinned deploy
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "ERROR" and "not reached" in v["reasons"][0] and v["halt_at_ms"] == ms(PIN_SWAP_UTC)
    # a full page of 1000 that never gets down to the pinned slot is the same
    cl.write_sigs["PD_PUMP"] = [{"signature": f"x{i}", "slot": 5000 + i, "blockTime": 1, "err": None} for i in range(1000)]
    v2 = run(make_rec(), tmp_path / "b", client=cl)
    assert v2["verdict"] == "ERROR" and "not reached" in v2["reasons"][0]


def test_non_upgrade_and_failed_txs_on_the_programdata_are_not_writes(tmp_path):
    cl = FakeClient(sample_blobs())
    cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    cl.write_sigs["PD_PUMP"].append({"signature": "failedw", "slot": 700, "blockTime": 1, "err": {"x": 1}})  # failed: skipped
    cl.write_sigs["PD_PUMP"].append({"signature": "setauth", "slot": 800, "blockTime": 1, "err": None})
    cl.write_txs["setauth"] = {"slot": 800, "blockTime": 1, "transaction": {"message": {"accountKeys": ["A", "PD_PUMP", c.LOADER_PROGRAM],
                               "instructions": [{"programIdIndex": 2, "data": mon.b58encode(struct.pack("<I", 4))}]}}, "meta": {"err": None}}  # SetAuthority, not Upgrade
    cl.write_sigs["PD_PUMP"].sort(key=lambda x: -x["slot"])
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "PASS" and sorted(v["per_write"]) == ["pump:1000", "pumpswap:1100"]


def test_newest_write_must_match_the_records_deploy_slot(tmp_path):
    cl = FakeClient(sample_blobs())
    cl.set_writes({"PD_PUMP": [(999, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "ERROR" and "!= the record's deploy slot" in v["reasons"][0]


def test_hard_flag_uses_the_earliest_unreviewed_write(tmp_path):
    rec = make_rec()
    rec["halt"]["flags"]["boost_disabled"] = flag(True)
    v = run(rec, tmp_path, client=multi_client())
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(EARLY_SWAP)


# ---- reviewer B2 / QP B-1: the 10-19 case (a 15:46:16Z write plus the 17:47 writes) when the enumeration fails
def history_recs():
    """Daily records before the redeploys: the pins are still the live pins (pump 100, pumpswap 200)."""
    return [make_rec(redeploy=False, run_utc="2026-10-18T07:11:13Z"), make_rec(redeploy=False, run_utc="2026-10-19T07:12:59Z")]


def swap_not_reached_client():
    cl = multi_client()  # PumpSwap writes at 15:46:16Z and 17:47:20Z, pump at 17:47:02Z
    cl.write_sigs["PD_SWAP"] = [x for x in cl.write_sigs["PD_SWAP"] if not x["signature"].startswith("pinned")]  # PumpSwap paging fails: the pinned slot is not reached
    return cl


def test_enumeration_failure_in_the_error_path_halts_no_later_than_the_last_record_at_the_pinned_slot(tmp_path):
    v = run(make_rec(), tmp_path, client=swap_not_reached_client(), history=history_recs())
    assert v["verdict"] == "ERROR" and "pumpswap" in v["reasons"][0]
    assert v["halt_at_ms"] == ms("2026-10-19T07:12:59Z") < ms(EARLY_SWAP) < ms(DEPLOY_SWAP)  # not 17:47:20Z, not even 15:46:16Z
    assert c.read_state(tmp_path / "state.jsonl")["pumpswap:1100"]["verdict"] == "ERROR"
    v2 = run(make_rec(), tmp_path / "b", client=swap_not_reached_client())  # no earlier record at the pinned slot: the pinned deploy's own instant
    assert v2["halt_at_ms"] == ms(PIN_SWAP_UTC)


def test_enumeration_failure_in_the_hard_flag_path_uses_the_same_bound(tmp_path):
    rec = make_rec()
    rec["halt"]["flags"]["boost_disabled"] = flag(True)
    v = run(rec, tmp_path, client=swap_not_reached_client(), history=history_recs())
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms("2026-10-19T07:12:59Z") and "write enumeration failed for pumpswap" in v["reasons"][-1]
    cl = multi_client()
    cl.fail = mon.RpcUnreachable("down")
    v2 = run(rec, tmp_path / "b", client=cl, history=history_recs())  # every program fails: still bounded, the halt is not lost
    assert v2["verdict"] == "HALT" and v2["halt_at_ms"] == ms("2026-10-19T07:12:59Z")


def test_other_programs_keep_their_earliest_unreviewed_write_when_one_enumeration_fails(tmp_path):
    cl = multi_client()
    cl.write_sigs["PD_PUMP"] = [x for x in cl.write_sigs["PD_PUMP"] if not x["signature"].startswith("pinned")]  # pump fails; pumpswap enumerates
    rec = make_rec()
    rec["halt"]["flags"]["boost_disabled"] = flag(True)
    # history: the last record at the PUMP pinned slot was 2026-10-19T07:12:59Z, later than the enumerated PumpSwap write? no: earlier. The earliest of all wins.
    v = run(rec, tmp_path, client=cl, history=history_recs())
    assert v["halt_at_ms"] == ms("2026-10-19T07:12:59Z") and v["halt_at_ms"] <= ms(EARLY_SWAP)
    h2 = [make_rec(redeploy=False, run_utc="2026-10-19T16:00:00Z")]  # a record at the pinned slot AFTER the enumerated 15:46:16Z write cannot exist; the enumerated write is the earlier bound
    v2 = run(rec, tmp_path / "b", client=cl, history=h2)
    assert v2["halt_at_ms"] == ms(EARLY_SWAP)


def test_sticky_failed_state_bounds_a_later_enumeration_failure(tmp_path):
    state = tmp_path / "state.jsonl"
    c.append_state(state, {"key": "pumpswap:900", "program": "pumpswap", "slot": 900, "deploy_utc": "2026-10-19T06:00:00Z", "verdict": "FAIL", "reasons": ["x"]})
    b = c.failclosed_bound_ms("pumpswap", make_rec(), MON_PINS, history_recs(), c.read_state(state))
    assert b == ms("2026-10-19T06:00:00Z")


def test_upgrade_through_a_multisig_inner_instruction_is_a_write(tmp_path):
    cl = FakeClient(sample_blobs())
    cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    for sig, tx in cl.write_txs.items():  # the real shape: the loader Upgrade is a CPI inside a Squads instruction
        msg = tx["transaction"]["message"]
        msg["accountKeys"].append("SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf")
        msg["instructions"] = [{"programIdIndex": 3, "data": mon.b58encode(b"\xc2\x08\xa1\x57")}]
        tx["meta"]["innerInstructions"] = [{"index": 0, "instructions": [{"programIdIndex": 2, "data": mon.b58encode(struct.pack("<I", c.LOADER_UPGRADE_TAG))}]}]
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "PASS" and sorted(v["per_write"]) == ["pump:1000", "pumpswap:1100"]


# ---- reviewer 6324690172 B1: a degraded record is never a quiet NONE
def test_record_without_flags_is_an_operational_error_not_none(tmp_path):
    for rec in ({**make_rec(redeploy=False), "halt": {}}, {k: v for k, v in make_rec(redeploy=False).items() if k != "halt"}, {**make_rec(redeploy=False), "halt": {"flags": {}}}):
        with pytest.raises(c.CheckError):
            c.triage(rec, MON_PINS, None)
    write_pins_files(tmp_path, build_pins())
    write_daily(tmp_path / "daily.jsonl", [{**make_rec(redeploy=False), "halt": {}}])
    assert c.main(argv(tmp_path), client=FakeClient([]), http_get=http_ok(), now_ms=lambda: NOW) == 11
    assert not (tmp_path / "v.json").exists()  # no verdict => the daily script applies nothing and appends nothing to the applied log (the dead-man is not reset)


def test_pins_are_compared_on_every_run_even_when_the_flag_says_false():
    rec = make_rec()
    rec["halt"]["flags"]["pins_changed"] = flag(False)  # a degraded monitor line: the programs differ from the pins but the flag is clear
    t = c.triage(rec, MON_PINS, None)
    assert t["action"] == "hard" and any("degraded" in r for r in t["hard_reasons"])  # the flag/pins disagreement is itself a hard reason ...
    assert {d["name"] for d in t["redeploys"]} == {"pump", "pumpswap"} and t["flag_halt_ms"] == ms(RUN_UTC)  # ... and the redeploys still bound the instant
    rec2 = make_rec(redeploy=False)
    rec2["accounts"]["pumpswap_global_config"]["sha256"] = "f" * 64
    rec2["halt"]["flags"]["pins_changed"] = flag(False)
    t2 = c.triage(rec2, MON_PINS, None)
    assert t2["action"] == "hard" and any("pumpswap_global_config" in r for r in t2["hard_reasons"]) and any("degraded" in r for r in t2["hard_reasons"])
    rec3 = make_rec(redeploy=False)
    del rec3["halt"]["flags"]["pins_changed"]  # absent flag AND a changed pin
    rec3["programs"]["fees"] = {"deploy_slot": 999, "deploy_utc": "2026-10-19T20:00:00Z"}
    assert c.triage(rec3, MON_PINS, None)["action"] == "hard"


def test_an_absent_flag_is_unevaluated_and_two_consecutive_runs_halt():
    rec, prev = make_rec(redeploy=False), make_rec(redeploy=False, run_utc="2026-10-19T07:11:27Z")
    for r in (rec, prev):
        del r["halt"]["flags"]["boost_disabled"]
    t = c.triage(rec, MON_PINS, prev)
    assert t["action"] == "hard" and any(r.startswith("boost_disabled: unevaluated on two") for r in t["hard_reasons"])
    assert c.triage(rec, MON_PINS, make_rec(redeploy=False, run_utc="2026-10-19T07:11:27Z"))["action"] == "none"  # only one run missing it
    rec["halt"]["flags"]["boost_share_low"] = {"evaluated": True}  # no boolean halt: malformed => unevaluated, not clear
    assert c._norm_flags(rec)["boost_share_low"]["evaluated"] is False


def test_unfetchable_and_non_migrate_txs_are_counted_not_dropped_silently(tmp_path):
    cl = FakeClient(sample_blobs(), n_migrate=c.MIGRATE_K + 3)
    del cl.txs["sig0"]  # unfetchable
    cl.txs["sig1"] = {"slot": cl.txs["sig1"]["slot"], "blockTime": cl.txs["sig1"]["blockTime"], "transaction": {"message": {"accountKeys": []}}, "meta": {"logMessages": [], "innerInstructions": []}}
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "PASS" and v["detail"]["migrate_sample"]["migrate_skipped"] == {"unfetchable": 1, "not_a_migrate_tx": 1}


def test_reviewed_programdata_is_labelled_record_only():
    shipped = json.loads((Path(c.__file__).with_name("a3_redeploy_pins.json")).read_text())
    assert "RECORD ONLY" in shipped["reviewed_programdata"]["note"] and "WithdrawEvent" in shipped["reviewed_programdata"]["note"]


def test_not_reached_error_reports_the_number_of_signatures_read(tmp_path):
    cl = FakeClient(sample_blobs())
    cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    cl.write_sigs["PD_SWAP"] = [x for x in cl.write_sigs["PD_SWAP"] if not x["signature"].startswith("pinned")]
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "ERROR" and "after reading 1 signatures" in v["reasons"][0]


def test_broken_previous_record_does_not_discard_todays_valid_halt(tmp_path):
    rec = make_rec(redeploy=False)
    rec["halt"]["flags"]["boost_disabled"] = flag(True, reason="boost off")
    for n, prev in enumerate(({**make_rec(redeploy=False, run_utc="2026-10-19T07:11:27Z"), "halt": {}}, {"run_utc": "2026-10-19T07:11:27Z"})):
        t = c.triage(rec, MON_PINS, prev)  # yesterday's flagless record: all-unevaluated, today's hard BOOST flag still halts
        assert t["action"] == "hard" and t["flag_halt_ms"] == ms(RUN_UTC) and any(r.startswith("boost_disabled: boost off") for r in t["hard_reasons"])
        v = run(rec, tmp_path / str(n), prev=prev)
        assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(RUN_UTC)


# ---- M-1 failure (job #1311): 'write candidate unreadable' on the public RPC
class FlakyTx(FakeClient):
    """The first `nulls` getTransaction answers per signature are null (what a lagging public backend's -32004/-32009 look like through mon.RpcClient)."""

    def __init__(self, *a, nulls=0, **kw):
        super().__init__(*a, **kw)
        self.nulls, self.seen = nulls, {}

    def call(self, method, params):
        if method == "getTransaction" and params[0].startswith("w"):
            self.seen[params[0]] = self.seen.get(params[0], 0) + 1
            if self.seen[params[0]] <= self.nulls:
                self.calls += 1
                return None
        return super().call(method, params)


def test_transient_null_candidate_is_retried_with_backoff_and_then_read(tmp_path, _no_real_sleep):
    cl = FlakyTx(sample_blobs(), nulls=2)
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "PASS" and sorted(v["per_write"]) == ["pump:1000", "pumpswap:1100"]
    assert _no_real_sleep[:2] == [c.TX_RETRY_BACKOFF_S, c.TX_RETRY_BACKOFF_S * 2]  # exponential, bounded


def test_a_candidate_that_stays_null_fails_closed_after_the_bounded_tries(tmp_path, _no_real_sleep):
    cl = FlakyTx(sample_blobs(), nulls=99)
    v = run(make_rec(), tmp_path, client=cl)
    assert v["verdict"] == "ERROR" and f"unreadable after {c.TX_RETRY_ATTEMPTS} tries" in v["reasons"][0]
    assert max(cl.seen.values()) == c.TX_RETRY_ATTEMPTS and len(_no_real_sleep) <= 2 * (c.TX_RETRY_ATTEMPTS - 1)


def test_real_errors_are_not_retried_as_nulls():
    class Boom:
        calls = 0

        def call(self, method, params):
            Boom.calls += 1
            raise mon.RpcError("rpc error -32602 invalid")
    with pytest.raises(mon.RpcError):
        c.fetch_tx_retry(Boom(), "sig")
    assert Boom.calls == 1


# ---- optional archival RPC for the enumeration only
SECRET = "k3y-SHOULD-NEVER-APPEAR"


def test_env_file_url_is_read_without_echo(tmp_path):
    f = tmp_path / "h.env"
    f.write_text(f'# comment\nOTHER=1\nexport HELIUS_RPC_URL="https://mainnet.example.test/?api-key={SECRET}"\n')
    assert c.rpc_url_from_env_file(str(f)).endswith(SECRET)
    f.write_text(f"HELIUS_API_KEY={SECRET}\n")
    assert c.rpc_url_from_env_file(str(f)) == f"https://mainnet.helius-rpc.com/?api-key={SECRET}"
    f.write_text(f"MY_URL=https://x.test/{SECRET}\n")
    assert c.rpc_url_from_env_file(str(f), "MY_URL").endswith(SECRET)
    for bad in ("NOTHING=1\n", f"HELIUS_RPC_URL=http://insecure/{SECRET}\n"):
        f.write_text(bad)
        with pytest.raises(c.CheckError) as ei:
            c.rpc_url_from_env_file(str(f))
        assert SECRET not in str(ei.value)
    with pytest.raises(c.CheckError) as ei:
        c.rpc_url_from_env_file(str(tmp_path / "missing.env"))


def test_enumeration_uses_the_separate_client_and_the_samples_do_not(tmp_path, capsys):
    write_pins_files(tmp_path, build_pins())
    write_daily(tmp_path / "daily.jsonl", [make_rec()])
    main_cl = FakeClient(sample_blobs(), fail_on={"getSignaturesForAddress_pd"})
    main_cl.write_sigs.clear()  # the main client knows no programdata signatures: enumeration must NOT go through it
    enum_cl = FakeClient([])
    enum_cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})
    enum_cl.fail_on = {"getBlock", "getMultipleAccounts"}  # and the archival client must never serve the samples
    rc = c.main(argv(tmp_path), client=main_cl, enum_client=enum_cl, http_get=http_ok(), now_ms=lambda: NOW)
    out = json.loads((tmp_path / "v.json").read_text())
    assert rc == 0 and out["verdict"] == "PASS" and out["enumeration"]["separate_rpc"] is True


def test_main_with_env_file_never_prints_the_url(tmp_path, capsys, monkeypatch):
    write_pins_files(tmp_path, build_pins())
    write_daily(tmp_path / "daily.jsonl", [make_rec()])
    f = tmp_path / "h.env"
    f.write_text(f"HELIUS_API_KEY={SECRET}\n")
    seen = {}

    class Rec(FakeClient):
        pass
    enum_cl = Rec([])
    enum_cl.set_writes({"PD_PUMP": [(1000, DEPLOY_PUMP)], "PD_SWAP": [(1100, DEPLOY_SWAP)]})

    def fake_rpc(url, **kw):
        seen["url"] = url
        return enum_cl
    monkeypatch.setattr(c.mon, "RpcClient", fake_rpc)
    rc = c.main(argv(tmp_path, "--enum-rpc-env-file", str(f)), client=FakeClient(sample_blobs()), http_get=http_ok(), now_ms=lambda: NOW)
    cap = capsys.readouterr()
    assert rc == 0 and SECRET in seen["url"]
    assert SECRET not in cap.out and SECRET not in cap.err and SECRET not in (tmp_path / "v.json").read_text() and SECRET not in (tmp_path / "st.jsonl").read_text()
    bad = tmp_path / "bad.env"
    bad.write_text("X=1\n")
    assert c.main(argv(tmp_path, "--enum-rpc-env-file", str(bad)), client=FakeClient([]), http_get=http_ok(), now_ms=lambda: NOW) == 11


def test_enum_only_prints_writes_and_call_count(tmp_path, capsys):
    cl = FakeClient([])
    cl.set_writes({"PD_PUMP": [(900, EARLY_SWAP), (1000, DEPLOY_PUMP)]})
    rc = c.main(["--enum-only", "pump", "--programdata", "PD_PUMP", "--pinned-slot", "100", "--newest-slot", "1000"], enum_client=cl)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and [w["new"] for w in out["writes"]] == [900, 1000] and out["program"] == "pump"
    assert c.main(["--enum-only", "pump", "--programdata", "PD_PUMP", "--pinned-slot", "100", "--newest-slot", "999"], enum_client=cl) == 11
    assert c.main(["--enum-only", "pump"], enum_client=cl) == 11
