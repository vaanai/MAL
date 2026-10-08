"""Tests for tools/pump_structure_monitor.py. Fixture-based: no network.

Recorded fixtures (tools/fixtures/pump_structure_monitor/, public mainnet RPC, 2026-10-08): config/gate/program
accounts, programdata slices, performance samples, epoch info (all current state), and one 2026-09-20
exploration-period pool (migrate tx, completing tx, BOOST signature list, Pool account). Constructed inputs are
labelled as such: nothing on chain yet shows a PostCompleteBuyEvent or a PumpSwap v2 trade instruction in our
samples, so those two are built from the IDL layout / the audit's instruction names.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import random
import re
import struct
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import pump_structure_monitor as m

FX = Path(__file__).parent / "fixtures" / "pump_structure_monitor"
PINS = json.loads(m.DEFAULT_PINS.read_text())
NOW = 1_790_100_000
KEEPER = "HTVZVEQMBsNanubDPTs3CxDAEGNFQHJY8c1441iy2S5r"  # GlobalConfig.boost_authority in the recorded account


def fx(name: str):
    return json.loads((FX / name).read_text())


REAL_URLLIB_GET = m.urllib_get  # the real unauthenticated GET, kept for the one test that inspects its request
TODAY = datetime.fromtimestamp(NOW, timezone.utc).date()


def acct(data: bytes, owner: str = "11111111111111111111111111111111") -> dict:
    return {"data": [base64.b64encode(data).decode(), "base64"], "lamports": 1, "owner": owner}


# ---------------------------------------------------------------------------------------------
# fake node behind the real RpcClient (so the JSON/retry path is exercised too)
# ---------------------------------------------------------------------------------------------
class NodeError(Exception):
    """Raised by a handler to make the fake node answer with a JSON-RPC error (not retried unless the code says so)."""

    def __init__(self, code=-32602):
        self.code = code


class Node:
    def __init__(self, handlers):
        self.handlers = handlers
        self.methods: list[str] = []

    def __call__(self, url, body, timeout):
        req = json.loads(body)
        self.methods.append(req["method"])
        try:
            reply = {"result": self.handlers[req["method"]](req["params"])}
        except NodeError as exc:
            reply = {"error": {"code": exc.code, "message": "constructed failure"}}
        return 200, {}, json.dumps({"jsonrpc": "2.0", "id": 1, **reply}).encode()


def client_for(node, **kw) -> m.RpcClient:
    return m.RpcClient("https://rpc.test/", min_interval=0, sleep=lambda s: None, transport=node, **kw)


def config_result(mutate=None):
    res = fx("config_accounts.json")
    if mutate:
        mutate(res["value"])
    return res


def flip_global_config(values):  # change one byte (protocol_fee_basis_points) of the PumpSwap GlobalConfig
    data = bytearray(base64.b64decode(values[2]["data"][0]))
    data[48] ^= 1
    values[2]["data"][0] = base64.b64encode(bytes(data)).decode()


def set_boost_enabled(values, flag: int):
    data = bytearray(base64.b64decode(values[2]["data"][0]))
    data[m.GLOBAL_CONFIG_BOOST_ENABLED] = flag
    values[2]["data"][0] = base64.b64encode(bytes(data)).decode()


# ---------------------------------------------------------------------------------------------
# constructed events and txs
# ---------------------------------------------------------------------------------------------
def k(i: int, j: int = 0) -> bytes:
    return bytes([i, j]) + bytes(30)


USDC = m.b58decode(m.USDC_MINT)
PUMP_QUOTE = k(77, 7)  # a pump-token quote mint (constructed): the "other" class


def migration_event(mint, curve, pool, quote=bytes(32), ts=1_790_000_000) -> bytes:
    blob = m.DISC_MIGRATE + bytes(32) + mint + struct.pack("<QQQ", 1, 2, 3) + curve + struct.pack("<q", ts) + pool + quote
    assert len(blob) == 200
    return blob


def complete_event(mint, curve, ts=1_790_000_000) -> bytes:
    return m.DISC_COMPLETE + bytes(32) + mint + curve + struct.pack("<q", ts) + bytes(32)


def post_complete_event(mint, curve, ts=1_790_000_000) -> bytes:
    """Constructed from the pump IDL field order (user, mint, bonding_curve, quote_mint, timestamp, base_out, quote_in, ...)."""
    blob = m.DISC_POST_COMPLETE_BUY + bytes(32) + mint + curve + bytes(32) + struct.pack("<qQQ", ts, 5, 6) + bytes(40)
    assert len(blob) >= 160
    return blob


def data_line(blob: bytes) -> str:
    return "Program data: " + base64.b64encode(blob).decode()


def mini_tx(slot, bt, logs, keys=None, pre=None, post=None, signers=1) -> dict:
    keys = keys or [m.NATIVE_SOL]
    return {
        "slot": slot, "blockTime": bt,
        "transaction": {"message": {"accountKeys": keys, "header": {"numRequiredSignatures": signers}}},
        "meta": {"logMessages": logs, "preBalances": pre or [0] * len(keys), "postBalances": post or [0] * len(keys), "innerInstructions": []},
    }


# ---------------------------------------------------------------------------------------------
# change watch fixtures: a fake GitHub (recorded commit list) and constructed programdata. No network anywhere.
# ---------------------------------------------------------------------------------------------
def docs_body(n_new: int = 0) -> bytes:
    """The recorded 30-commit list (public repo pump-fun/pump-public-docs, 2026-10-08, trimmed to sha/message/dates), with `n_new` constructed newer commits in front."""
    new = [{"sha": f"{0xdead00 + i:040x}", "commit": {"message": f"docs: constructed change {i}\n\nbody", "committer": {"date": f"2026-10-0{i + 1}T12:00:00Z"}}} for i in range(n_new)]
    return json.dumps(list(reversed(new)) + fx("docs_commits.json")).encode()


def fake_github(*responses, calls=None):
    """A stand-in for m.urllib_get. Each response is an exception instance (raised) or (status, body); the last one repeats."""
    script = list(responses) or [(200, docs_body())]
    seen = calls if calls is not None else []

    def get(url, timeout):
        seen.append(url)
        r = script[min(len(seen), len(script)) - 1]
        if isinstance(r, Exception):
            raise r
        return r[0], {}, r[1]

    return get


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("a test reached the network")

    monkeypatch.setattr(m.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(m, "urllib_get", fake_github())  # healthy GitHub by default


def _program_addrs() -> dict:
    node = Node({"getMultipleAccounts": lambda p: config_result()})
    return m.stage_accounts(client_for(node), fx("epoch_info.json"), NOW)["program_accounts"]


PROGRAMDATA_ADDR = _program_addrs()  # program name -> programdata address, from the recorded program accounts


def programdata_bytes(name: str, tweak: bytes = b"", slot: int | None = None) -> bytes:
    """Constructed UpgradeableLoaderState::ProgramData (tag 3, slot, Some(authority)) followed by a stand-in binary."""
    slot = PINS["programs"][name]["deploy_slot"] if slot is None else slot
    return struct.pack("<IQ", 3, slot) + b"\x01" + bytes(32) + (name.encode() + b"-elf") * 40 + tweak


def with_program_hashes(pins: dict) -> dict:
    pins = copy.deepcopy(pins)
    for name, p in pins["programs"].items():
        data = programdata_bytes(name)
        p["sha256"], p["data_len"] = hashlib.sha256(data).hexdigest(), len(data)
    return pins


TEST_PINS = with_program_hashes(PINS)  # the committed pins, with the program hashes of the constructed programdata


class Chain:
    """A tiny constructed chain: graduations (migrate tx + completing tx + BOOST slices + Pool account) plus the recorded config."""

    def __init__(self, n=10, mayhem=(0, 1), no_boost=(), synthetic=(), config=None, slices=29, last_slice_s=342, fail_pools=False, bad_txs=(), slice_signer=KEEPER, usdc=(), other=(), tweak=None):
        self.fail_pools = fail_pools
        self.bad_txs = set(bad_txs)  # signatures whose getTransaction answers with a JSON-RPC error
        self.sigs: dict[str, list] = {m.MIGRATION_FEE_ACCOUNT: []}
        self.txs: dict[str, dict] = {}
        self.pools: dict[str, dict] = {}
        self.mints: list[str] = []
        self.config = config or config_result()
        self.all_ids: list[str] = []
        self.programdata = {PROGRAMDATA_ADDR[name]: programdata_bytes(name, (tweak or {}).get(name, b"")) for name in PROGRAMDATA_ADDR}
        for i in range(n):
            mint, curve, pool = k(i + 1, 1), k(i + 1, 2), k(i + 1, 3)
            smint, scurve, spool = m.b58encode(mint), m.b58encode(curve), m.b58encode(pool)
            self.mints.append(smint)
            bt, slot = NOW - 600 - 40 * i, 1_000_000 - 100 * i
            boost = i not in mayhem and i not in no_boost
            logs = [f"Program {m.PUMP_PROGRAM} invoke [1]", "Program log: Instruction: MigrateV2"]
            if boost:
                logs += [f"Program {m.PUMPSWAP_PROGRAM} invoke [2]", "Program log: Instruction: InitBoost", f"Program {m.PUMPSWAP_PROGRAM} success"]
            quote = USDC if i in usdc else PUMP_QUOTE if i in other else bytes(32)
            logs += [data_line(migration_event(mint, curve, pool, quote=quote, ts=bt)), f"Program {m.PUMP_PROGRAM} success"]
            vault = m.boost_vault(spool)
            budget = 17_585_993_728
            self.txs[f"mig{i}"] = mini_tx(slot, bt, logs, [m.NATIVE_SOL, vault], [0, 0], [0, budget if boost else 0])
            self.sigs[m.MIGRATION_FEE_ACCOUNT].append({"signature": f"mig{i}", "slot": slot, "blockTime": bt, "err": None})
            # completing tx on the curve (+ PostCompleteBuyEvent when synthetic)
            clogs = [data_line(complete_event(mint, curve))]
            if i in synthetic:
                clogs.append(data_line(post_complete_event(mint, curve)))
            self.txs[f"cmp{i}"] = mini_tx(slot - 1, bt - 1, clogs)
            self.sigs[scurve] = [{"signature": f"cmp{i}", "slot": slot - 1, "blockTime": bt - 1, "err": None}]
            self.pools[spool] = acct(bytes(243) + bytes([1 if i in mayhem else 0]) + bytes(57), m.PUMPSWAP_PROGRAM)
            if boost:
                auth = m.boost_vault_authority(spool)
                lst = []
                for s in range(slices):
                    t = bt + (last_slice_s - 12 * (slices - 1 - s))
                    sig = f"bst{i}_{s}"
                    lst.append({"signature": sig, "slot": slot + 5 + s * 40, "blockTime": t, "err": None, "transactionIndex": 3})
                    self.txs[sig] = mini_tx(slot + 5 + s * 40, t, [f"Program {m.PUMPSWAP_PROGRAM} invoke [1]", "Program log: Instruction: BoostBuyAndBurn", f"Program {m.PUMPSWAP_PROGRAM} success"], [slice_signer])
                self.sigs[auth] = list(reversed(lst)) + [{"signature": f"mig{i}", "slot": slot, "blockTime": bt, "err": None}]
            self.all_ids += [smint, scurve, spool, f"mig{i}", f"cmp{i}"]
        # noise the sampler must skip: a failed tx, a too-young graduation, a non-migrate success
        mig = self.sigs[m.MIGRATION_FEE_ACCOUNT]
        mig.insert(0, {"signature": "young", "slot": 2_000_000, "blockTime": NOW - 100, "err": None})
        mig.insert(1, {"signature": "failed", "slot": 1_999_000, "blockTime": NOW - 700, "err": {"InstructionError": [0, "x"]}})
        mig.insert(2, {"signature": "other", "slot": 1_998_000, "blockTime": NOW - 701, "err": None})
        self.txs["other"] = mini_tx(1_998_000, NOW - 701, ["Program log: Instruction: Buy"])
        self.txs["young"] = mini_tx(2_000_000, NOW - 100, ["Program log: Instruction: Buy"])  # read only by a run made a day later
        trade_logs = [f"Program {m.PUMPSWAP_PROGRAM} invoke [1]", "Program log: Instruction: BuyV2", f"Program {m.PUMPSWAP_PROGRAM} success",
                      f"Program {m.PUMPSWAP_PROGRAM} invoke [1]", "Program log: Instruction: Sell", f"Program {m.PUMPSWAP_PROGRAM} success"]
        self.sigs[m.PUMPSWAP_PROGRAM] = [{"signature": f"amm{j}", "slot": 3_000_000, "blockTime": NOW - 5, "err": None} for j in range(50)]
        for j in range(50):
            self.txs[f"amm{j}"] = mini_tx(3_000_000, NOW - 5 - j % 3, trade_logs)

    def multi(self, params):
        keys, opts = params
        if opts.get("dataSlice", {}).get("length") == 45:
            return fx("programdata.json")
        if keys[0] in self.pools:
            if self.fail_pools:
                raise NodeError()
            return {"context": {"slot": 1}, "value": [self.pools.get(a) for a in keys]}
        if keys[0] == m.GATES["350ms"] or keys[0] == m.BONDING_FEE_CONFIG:
            return self.config
        return {"context": {"slot": 1}, "value": [None for _ in keys]}  # boost vaults: drained

    def sigs_for(self, params):
        lst = self.sigs.get(params[0], [])
        before = (params[1] if len(params) > 1 else {}).get("before")
        for i, s_ in enumerate(lst):
            if before and s_["signature"] == before:
                return lst[i + 1 :]
        return lst

    def account_info(self, params):
        data = self.programdata.get(params[0])
        return {"context": {"slot": 1}, "value": None if data is None else acct(data, "BPFLoaderUpgradeab1e11111111111111111111111")}

    def get_tx(self, params):
        if params[0] in self.bad_txs:
            raise NodeError()
        return self.txs[params[0]]

    def node(self) -> Node:
        return Node({
            "getEpochInfo": lambda p: fx("epoch_info.json"),
            "getRecentPerformanceSamples": lambda p: fx("perf_samples.json"),
            "getMultipleAccounts": self.multi,
            "getSignaturesForAddress": self.sigs_for,
            "getAccountInfo": self.account_info,
            "getTransaction": self.get_tx,
            "getBlockTime": lambda p: 1_790_000_000,
        })


# ---------------------------------------------------------------------------------------------
# decoders on recorded data
# ---------------------------------------------------------------------------------------------
def test_pda_and_base58_vectors():
    assert m.b58decode(m.b58encode(bytes(3) + b"\x01\xff")) == bytes(3) + b"\x01\xff"
    pool = "8wwzq5r9mGGD81e623wMZ9XFjiksn4ynMr9wCq2G8rYY"  # 2026-09-20 pool; addresses from the recorded tx
    assert m.boost_vault_authority(pool) == "CWYLYjH2fRL4dKXhftgYZXukWA5eP4rLXmuawF2LBrp9"
    assert m.boost_vault(pool) == "7SUagWJmyz6fpjZGTt1uLGULUhmR2SZSxWhfp9BgKJYy"


def test_pda_matches_solders_when_available():
    solders = pytest.importorskip("solders.pubkey")
    for i in range(20):
        pool = m.b58encode(k(i, 9))
        want = solders.Pubkey.find_program_address([b"boost_vault", k(i, 9)], solders.Pubkey.from_string(m.PUMPSWAP_PROGRAM))
        assert (m.boost_vault_authority(pool), m.find_program_address([b"boost_vault", k(i, 9)], m.PUMPSWAP_PROGRAM)[1]) == (str(want[0]), want[1])


def test_slot_time_median_and_edges():
    st = m.decode_slot_time(fx("perf_samples.json"))
    assert st["n_samples"] == 30 and st["ms_per_slot_median"] == pytest.approx(268.46, abs=0.01)
    assert m.decode_slot_time([{"samplePeriodSecs": 60, "numSlots": 0}, {"samplePeriodSecs": 60, "numSlots": 300}])["ms_per_slot_median"] == 200.0
    assert m.decode_slot_time(None)["ms_per_slot_median"] is None


def test_feature_gates_recorded_pending_and_absent():
    vals = fx("config_accounts.json")["value"]
    gates = [m.decode_feature_gate(m._account_bytes(v)) for v in vals[4:8]]
    assert [g["activated_at_slot"] for g in gates] == [440208000, 441936000, 447552000, 454464000]
    assert gates[3]["activated_epoch"] == 1052
    assert m.decode_feature_gate(bytes(9)) == {"status": "pending"}
    assert m.decode_feature_gate(None) == {"status": "absent"}


def test_fee_configs_recorded():
    vals = fx("config_accounts.json")["value"]
    bond = m.decode_fee_config(m._account_bytes(vals[0]))
    assert bond["flat_bps"] == [0, 95, 30] and bond["tier0_total_bps"] == 125 and bond["n_tiers"] == 1 and bond["bytes_consumed"] == 177
    swap = m.decode_fee_config(m._account_bytes(vals[1]))
    assert swap["tier0_bps"] == [2, 93, 30] and swap["tier0_total_bps"] == 125 and swap["n_tiers"] == 25 and swap["bytes_consumed"] == 2097
    assert m.decode_fee_config(b"\x00" * 20) is None


def test_global_config_recorded_and_mutated():
    vals = fx("config_accounts.json")["value"]
    raw = m._account_bytes(vals[2])
    gc = m.decode_global_config(raw)
    assert gc == {"len": 949, "layout_ok": True, "boost_enabled": True, "boost_authority": KEEPER}
    off = bytearray(raw)
    off[m.GLOBAL_CONFIG_BOOST_ENABLED] = 0
    assert m.decode_global_config(bytes(off))["boost_enabled"] is False
    assert m.decode_global_config(raw[:900])["boost_enabled"] is None
    assert m.decode_global_config(raw + b"\x00")["layout_ok"] is False


def test_programdata_and_program_accounts_recorded():
    vals = fx("config_accounts.json")["value"]
    assert [m.decode_program_account(m._account_bytes(v)) for v in vals[8:11]] == [
        "B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu", "6naEzKeUuFh1Jeeu51NXQgr5qkXgXtc9WKNct4xynVJc", "75Uu23mqWBb8LM8vDppqC1mQAnCcBuLXhVaDezVMQLRw"]
    assert [m.decode_programdata_slot(m._account_bytes(v)) for v in fx("programdata.json")["value"]] == [452654932, 452654882, 452655002]
    assert m.decode_programdata_slot(bytes(45)) is None and m.decode_programdata_slot(None) is None


def test_pool_mayhem_flag_recorded_and_mutated():
    raw = m._account_bytes(fx("pool_account_sep20.json")["value"])
    assert len(raw) == 301 and m.decode_pool_mayhem(raw) is False
    on = bytearray(raw)
    on[m.POOL_MAYHEM_OFFSET] = 1
    assert m.decode_pool_mayhem(bytes(on)) is True
    assert m.decode_pool_mayhem(raw[:100]) is None and m.decode_pool_mayhem(None) is None


def test_migrate_tx_recorded_emit_cpi_path():
    g = m.parse_migrate_tx(fx("migrate_tx_sep20.json"))
    assert (g["mint"], g["pool"]) == ("36tgp72XmYWGk7btzSyQqrMChTeM6xRFBA94qgampump", "8wwzq5r9mGGD81e623wMZ9XFjiksn4ynMr9wCq2G8rYY")
    assert g["quote_wsol"] and g["init_boost"] and g["migrate_ix"] == "MigrateV2" and g["slot"] == 448641728
    assert g["budget_lamports"] == 17_585_993_728 and not g["complete_in_tx"] and not g["post_complete_in_tx"]


def test_migrate_tx_log_path_and_variants():
    tx = fx("migrate_tx_sep20.json")
    blob = next(b for b in m.tx_event_blobs(tx) if b[:8] == m.DISC_MIGRATE)
    as_logs = copy.deepcopy(tx)
    as_logs["meta"]["innerInstructions"] = []
    as_logs["meta"]["logMessages"].append(data_line(blob))  # the Oct-8 form: event as a log line
    assert m.parse_migrate_tx(as_logs) == m.parse_migrate_tx(tx)
    no_boost = copy.deepcopy(as_logs)
    no_boost["meta"]["logMessages"] = [x for x in no_boost["meta"]["logMessages"] if "InitBoost" not in x]
    assert m.parse_migrate_tx(no_boost)["init_boost"] is False
    other = copy.deepcopy(as_logs)
    other["meta"]["logMessages"][-1] = data_line(blob[:168] + k(7) + blob[200:])  # quote mint is not WSOL / native SOL
    g = m.parse_migrate_tx(other)
    assert g["quote_wsol"] is False and g["budget_lamports"] is None
    assert m.parse_migrate_tx(mini_tx(1, 1, ["Program log: Instruction: Buy"])) is None


def test_completion_recorded_and_synthetic_constructed():
    mig, comp = m.parse_migrate_tx(fx("migrate_tx_sep20.json")), fx("completing_tx_sep20.json")
    assert m.completion_info(comp, mig["mint"]) == {"slot": 448641728, "synthetic": False, "synthetic_mint_match": None}
    assert m.completion_info(comp, m.b58encode(k(3))) is None
    mint, curve = m.b58decode(mig["mint"]), m.b58decode(mig["curve"])

    def with_event(blob):
        tx = copy.deepcopy(comp)
        tx["meta"]["logMessages"].append(data_line(blob))
        return m.completion_info(tx, mig["mint"])

    assert with_event(post_complete_event(mint, curve)) == {"slot": 448641728, "synthetic": True, "synthetic_mint_match": True}
    # The discriminator alone counts (a kill-switch over-counts rather than misses); the mint match is only a refinement.
    assert with_event(post_complete_event(k(9), curve)) == {"slot": 448641728, "synthetic": True, "synthetic_mint_match": False}
    assert with_event(m.DISC_POST_COMPLETE_BUY + bytes(20)) == {"slot": 448641728, "synthetic": True, "synthetic_mint_match": None}  # layout changed / truncated


def test_program_instruction_attribution():
    logs = [f"Program {m.PUMP_PROGRAM} invoke [1]", "Program log: Instruction: Buy", f"Program {m.PUMPSWAP_PROGRAM} invoke [2]", "Program log: Instruction: InitBoost",
            f"Program {m.PUMPSWAP_PROGRAM} success", "Program log: Instruction: Migrate", f"Program {m.PUMP_PROGRAM} success", f"Program {m.PUMPSWAP_PROGRAM} invoke [1]",
            "Program log: Instruction: BuyV2", f"Program {m.PUMPSWAP_PROGRAM} failed: custom program error: 0x1"]
    assert m.program_instructions(logs, m.PUMP_PROGRAM) == ["Buy", "Migrate"]
    assert m.program_instructions(logs, m.PUMPSWAP_PROGRAM) == ["InitBoost", "BuyV2"]


def test_pumpswap_mix_counts_v2_share():
    chain = Chain(n=1)  # constructed trade logs: 50 txs x (1 BuyV2 + 1 Sell)
    mix = m.stage_pumpswap_mix(client_for(chain.node()), 40)
    assert (mix["trade_ix_v1"], mix["trade_ix_v2"], mix["v2_share"], mix["n_txs"]) == (40, 40, 0.5, 40)
    assert mix["ix_counts"] == {"BuyV2": 40, "Sell": 40}


def test_boost_profile_on_recorded_signature_list():
    sigs = fx("boost_sigs_sep20.json")  # 29 keeper slices + the migrate (funding) tx, 2026-09-20
    g = m.parse_migrate_tx(fx("migrate_tx_sep20.json"))
    g.update(sig=sigs[-1]["signature"], init_boost=True)
    txs = {s["signature"]: mini_tx(s["slot"], s["blockTime"], [f"Program {m.PUMPSWAP_PROGRAM} invoke [1]", "Program log: Instruction: BoostBuyAndBurn"], [KEEPER]) for s in sigs}
    node = Node({"getSignaturesForAddress": lambda p: sigs, "getTransaction": lambda p: txs[p[0]], "getMultipleAccounts": lambda p: {"value": [None]}})
    prof = m.profile_boost(client_for(node), [g], KEEPER)
    assert prof["slices"]["median"] == 29 and prof["last_slice_verified"] == 1 and prof["n_vault_drained"] == 1
    # the audit's s20 test read this pool as "29 slices, 327 s" = first-to-last span; the last slice is 329 s after the migrate tx
    assert (prof["first_slice_after_migrate_s"]["median"], prof["last_slice_after_migrate_s"]["median"], prof["first_to_last_span_s"]["median"]) == (2, 329, 327)
    assert prof["budget_sol"]["median"] == 17.586 and prof["sol_total"]["median"] == 17.586  # 17,585,993,728 lamports funded, vault drained (dist rounds to 4 dp)


def fixture_pool(sigs):
    g = m.parse_migrate_tx(fx("migrate_tx_sep20.json"))
    g.update(sig=sigs[-1]["signature"], init_boost=True)
    return g


def slice_node(sigs, keeper_from: int | None, fetch_log: list | None = None):
    """Recorded BOOST signature list; getTransaction answers keeper-signed from index `keeper_from` on, non-keeper before it."""
    order = {s["signature"]: i for i, s in enumerate(sigs)}  # 0 = newest

    def tx_for(p):
        s = sigs[order[p[0]]]
        ok = keeper_from is not None and order[p[0]] >= keeper_from
        logs = [f"Program {m.PUMPSWAP_PROGRAM} invoke [1]", "Program log: Instruction: BoostBuyAndBurn" if ok else "Program log: Instruction: Transfer"]
        return mini_tx(s["slot"], s["blockTime"], logs, [KEEPER if ok else m.b58encode(k(7))])

    return Node({"getSignaturesForAddress": lambda p: sigs, "getTransaction": tx_for, "getMultipleAccounts": lambda p: {"value": [None]}})


def test_last_slice_walks_back_past_non_keeper_txs():
    sigs = fx("boost_sigs_sep20.json")  # newest first
    g, node = fixture_pool(sigs), slice_node(sigs, keeper_from=3)  # the 3 newest signatures are not keeper txs
    prof = m.profile_boost(client_for(node), [g], KEEPER)
    assert g["boost"]["last_verified"] and g["boost"]["last_after_s"] == sigs[3]["blockTime"] - g["block_time"]
    assert node.methods.count("getTransaction") == 4 and prof["last_slice_verified"] == 1 and prof["last_slice_unverified"] == 0


def test_last_slice_is_unverified_not_guessed_when_no_keeper_tx_is_found():
    sigs = fx("boost_sigs_sep20.json")
    g, node = fixture_pool(sigs), slice_node(sigs, keeper_from=None)
    prof = m.profile_boost(client_for(node), [g], KEEPER, max_verify_fetches=5)
    assert node.methods.count("getTransaction") == 5  # bounded walk
    assert g["boost"]["last_verified"] is False and g["boost"]["last_after_s"] is None and g["boost"]["span_s"] is None
    assert prof["last_slice_after_migrate_s"] is None and prof["last_slice_unverified"] == 1 and prof["slices"]["median"] == 29
    g2, node2 = fixture_pool(sigs), slice_node(sigs, keeper_from=0)  # GlobalConfig unread: no keeper to check against, so no guess either
    prof2 = m.profile_boost(client_for(node2), [g2], None)
    assert "getTransaction" not in node2.methods and g2["boost"]["last_after_s"] is None and prof2["last_slice_unverified"] == 1


def test_pool_with_no_slices_counts_as_ended_at_zero_seconds():
    sigs = fx("boost_sigs_sep20.json")
    g, node = fixture_pool(sigs), slice_node(sigs[-1:], keeper_from=0)  # only the funding migrate tx
    prof = m.profile_boost(client_for(node), [g], KEEPER)
    assert prof["n_zero_slices"] == 1 and prof["last_slice_after_migrate_s"]["median"] == 0 and prof["last_slice_verified"] == 1


def test_skip_counts_and_stops_on_call_cap_or_dead_rpc():
    errs = Counter()
    assert m._skip(errs, "x", m.RpcError("a")) is False and m._skip(errs, "x", m.RpcUnreachable("b")) is False
    assert m._skip(errs, "x", m.RpcUnreachable("b")) is False and m._skip(errs, "x", m.RpcUnreachable("b")) is True  # third unreachable item
    assert m._skip(Counter(), "x", m.CallBudgetExceeded("cap")) is True
    assert errs == {"x: RpcError": 1, "x: RpcUnreachable": 3}


# ---------------------------------------------------------------------------------------------
# RPC client
# ---------------------------------------------------------------------------------------------
def test_client_retries_429_with_retry_after_then_succeeds():
    seq = [(429, {"Retry-After": "7"}, b"{}"), (503, {}, b""), (200, {}, b'{"result": 5}')]
    sleeps: list[float] = []
    c = m.RpcClient("https://rpc.test", min_interval=0, sleep=sleeps.append, transport=lambda u, b, t: seq.pop(0))
    assert c.call("getSlot", []) == 5
    assert c.calls["getSlot"] == 3 and c.retries == 2 and sleeps[0] == 7.0 and sleeps[1] == 4.0


def test_client_unreachable_after_retries_and_call_cap():
    def boom(u, b, t):
        raise OSError("down")

    c = m.RpcClient("https://rpc.test", min_interval=0, max_retries=2, sleep=lambda s: None, transport=boom)
    with pytest.raises(m.RpcUnreachable):
        c.call("getSlot", [])
    assert c.calls["getSlot"] == 3
    capped = m.RpcClient("https://rpc.test", min_interval=0, max_calls=2, sleep=lambda s: None, transport=lambda u, b, t: (200, {}, b'{"result": 1}'))
    capped.call("a", []), capped.call("b", [])
    with pytest.raises(m.CallBudgetExceeded):
        capped.call("c", [])


def test_client_skip_codes_and_rpc_errors():
    def reply(code):
        return lambda u, b, t: (200, {}, json.dumps({"error": {"code": code, "message": "x"}}).encode())

    assert m.RpcClient("u", min_interval=0, transport=reply(-32009)).call("getBlockTime", [1]) is None
    with pytest.raises(m.RpcError):
        m.RpcClient("u", min_interval=0, transport=reply(-32602)).call("getBlockTime", [1])


def test_fetch_tx_retries_at_the_version_the_node_names():
    seen: list[int] = []
    msgs = {1: "Transaction version (2) is not supported by the requesting client. Please try the request again with the following configuration parameter: \"maxSupportedTransactionVersion\": 2"}

    def transport(u, body, t):
        v = json.loads(body)["params"][1]["maxSupportedTransactionVersion"]
        seen.append(v)
        if v in msgs:
            return 200, {}, json.dumps({"error": {"code": -32015, "message": msgs[v]}}).encode()
        return 200, {}, b'{"result": {"slot": 1}}'

    assert m.fetch_tx(m.RpcClient("u", min_interval=0, transport=transport), "sig") == {"slot": 1}
    assert seen == [1, 2]


# ---------------------------------------------------------------------------------------------
# pins and halt flags
# ---------------------------------------------------------------------------------------------
def healthy():
    return {
        "accounts": {n: {"pinned": True, "sha256": PINS["accounts"][n]["sha256"]} for n in PINS["accounts"]},
        "programs": {n: {"deploy_slot": PINS["programs"][n]["deploy_slot"]} for n in PINS["programs"]},
        "global_config": {"boost_enabled": True, "layout_ok": True, "len": 949},
        "graduations": {"n_requested": 20, "n": 20, "boost_denominator": {"n_nonmayhem_wsol": 10, "n_init_boost": 10}, "wsol_all": {"n": 10, "n_init_boost": 10},
                        "synthetic": {"n_checked": 10, "n_synthetic": 0}},
        "boost": {"n_profiled": 10, "slices": {"n": 10, "median": 29}, "budget_sol": {"n": 10, "median": 17.586}, "last_slice_after_migrate_s": {"n": 10, "median": 337}},
        "slot_time": {"ms_per_slot_median": 268.0},
        "errors": [],
    }


def flags(rec, prev=268.0):
    halt, warn = m.compute_flags(rec, PINS, prev)
    return halt, warn


def test_committed_pins_match_the_recorded_chain_state():
    node = Node({"getMultipleAccounts": lambda p: fx("programdata.json") if p[1].get("dataSlice") else config_result(), "getBlockTime": lambda p: 0})
    c = client_for(node)
    acc = m.stage_accounts(c, fx("epoch_info.json"), NOW)
    rec = {"accounts": acc["accounts"], "programs": m.stage_programs(c, acc["program_accounts"], PINS)}
    cmp_ = m.compare_pins(rec, PINS)
    assert cmp_["changed"] == [] and cmp_["unpinned"] == [] and cmp_["unread"] == [] and len(cmp_["ok"]) == 6
    assert PINS["accounts"]["pumpswap_global_config"]["sha256"].startswith("a698009a419d0b8c")  # == repo fixture tools/fixtures/pumpswap/global_config.json
    assert all(p["deploy_utc"] for p in rec["programs"].values())  # unchanged slot: block time comes from the pin, no getBlockTime call
    assert "getBlockTime" not in node.methods


def test_unchanged_pins_no_halt():
    halt, warn = flags(healthy())
    assert not any(v["halt"] for v in halt.values()) and all(v["evaluated"] for v in halt.values())
    assert not any(v["warn"] for v in warn.values())


def test_changed_global_config_halts():
    node = Node({"getMultipleAccounts": lambda p: config_result(flip_global_config)})
    acc = m.stage_accounts(client_for(node), fx("epoch_info.json"), NOW)
    rec = healthy()
    rec["accounts"] = acc["accounts"]
    halt, _ = flags(rec)
    assert halt["pins_changed"]["halt"] and "pumpswap_global_config" in halt["pins_changed"]["reason"]
    assert "bonding_fee_config" not in halt["pins_changed"]["reason"]


def test_changed_fee_config_and_program_deploy_halt():
    rec = healthy()
    rec["accounts"]["bonding_fee_config"]["sha256"] = "0" * 64
    rec["programs"]["pump"]["deploy_slot"] += 1
    halt, _ = flags(rec)
    assert halt["pins_changed"]["halt"] and "bonding_fee_config" in halt["pins_changed"]["reason"] and "program_pump" in halt["pins_changed"]["reason"]


def test_null_pin_or_unread_account_is_not_a_change():
    pins = copy.deepcopy(PINS)
    pins["accounts"]["pumpswap_fee_config"]["sha256"] = None
    rec = healthy()
    rec["accounts"]["bonding_fee_config"]["sha256"] = None
    halt, _ = m.compute_flags(rec, pins, 268.0)
    assert not halt["pins_changed"]["halt"] and "unpinned" in halt["pins_changed"]["reason"] and "unread" in halt["pins_changed"]["reason"]


def test_boost_disabled_halts_only_on_a_trusted_layout():
    node = Node({"getMultipleAccounts": lambda p: config_result(lambda v: set_boost_enabled(v, 0))})
    gc = m.stage_accounts(client_for(node), fx("epoch_info.json"), NOW)["global_config"]
    rec = healthy()
    rec["global_config"] = gc
    assert flags(rec)[0]["boost_disabled"]["halt"]
    rec["global_config"] = dict(gc, layout_ok=False)
    h = flags(rec)[0]["boost_disabled"]
    assert not h["halt"] and not h["evaluated"]


def with_grads(rec, den, n_boost, n_checked, n_syn):
    rec["graduations"]["boost_denominator"] = {"n_nonmayhem_wsol": den, "n_init_boost": n_boost}
    rec["graduations"]["wsol_all"] = {"n": den, "n_init_boost": n_boost}
    rec["graduations"]["synthetic"] = {"n_checked": n_checked, "n_synthetic": n_syn}
    return rec


def test_share_rule_falls_back_to_all_wsol_and_only_judges_none_at_all():
    rec = with_grads(healthy(), 3, 3, 10, 0)  # only 3 non-mayhem WSOL graduations known
    rec["graduations"]["wsol_all"] = {"n": 10, "n_init_boost": 3}
    h = flags(rec)[0]["boost_share_low"]
    assert h["evaluated"] and not h["halt"] and "fallback" in h["reason"]  # mayhem pools lack InitBoost, so the share itself is not judged here
    rec["graduations"]["wsol_all"] = {"n": 10, "n_init_boost": 0}
    assert flags(rec)[0]["boost_share_low"]["halt"]
    rec["graduations"]["wsol_all"] = {"n": 4, "n_init_boost": 0}  # too few even on the fallback basis
    assert not flags(rec)[0]["boost_share_low"]["evaluated"]
    rec["graduations"].update(boost_denominator={"n_nonmayhem_wsol": 0, "n_init_boost": 0}, wsol_all={"n": 12, "n_init_boost": 0}, mayhem_stage_failed=True)
    h = flags(rec)[0]["boost_share_low"]
    assert h["halt"] and "mayhem flags unavailable" in h["reason"]


def test_boost_share_seven_of_ten_halts():
    assert flags(with_grads(healthy(), 10, 7, 10, 0))[0]["boost_share_low"]["halt"]
    assert not flags(with_grads(healthy(), 10, 8, 10, 0))[0]["boost_share_low"]["halt"]  # exactly 80% is not below 80%
    small = flags(with_grads(healthy(), 4, 1, 4, 0))[0]["boost_share_low"]
    assert not small["halt"] and not small["evaluated"]


def test_last_slice_300s_halts():
    rec = healthy()
    rec["boost"]["last_slice_after_migrate_s"]["median"] = 300
    assert flags(rec)[0]["boost_last_slice_early"]["halt"]
    rec["boost"]["last_slice_after_migrate_s"]["median"] = 315
    assert not flags(rec)[0]["boost_last_slice_early"]["halt"]
    few = healthy()
    few["boost"]["last_slice_after_migrate_s"]["n"] = 2  # only 2 pools with a keeper-verified last slice
    assert not flags(few)[0]["boost_last_slice_early"]["evaluated"]


def test_boost_budget_or_slice_count_change_over_20_percent_halts():
    for slices, budget, want in [(29, 17.586, False), (24, 17.586, False), (23, 17.586, True), (35, 17.586, True), (29, 21.0, False), (29, 21.2, True), (29, 14.0, True)]:
        rec = healthy()
        rec["boost"]["slices"]["median"], rec["boost"]["budget_sol"]["median"] = slices, budget
        assert flags(rec)[0]["boost_budget_or_slices_changed"]["halt"] is want, (slices, budget)


def test_synthetic_share_four_of_ten_halts():
    assert flags(with_grads(healthy(), 10, 10, 10, 4))[0]["synthetic_share_high"]["halt"]
    assert not flags(with_grads(healthy(), 10, 10, 10, 3))[0]["synthetic_share_high"]["halt"]
    assert not flags(with_grads(healthy(), 10, 10, 20, 7))[0]["synthetic_share_high"]["halt"]  # exactly 35% is not above 35%
    assert not flags(with_grads(healthy(), 10, 10, 3, 3))[0]["synthetic_share_high"]["evaluated"]


def test_ms_per_slot_warn_over_10_percent():
    rec = healthy()
    rec["slot_time"]["ms_per_slot_median"] = 212.0
    assert flags(rec, prev=268.0)[1]["ms_per_slot_moved"]["warn"]
    rec["slot_time"]["ms_per_slot_median"] = 280.0
    assert not flags(rec, prev=268.0)[1]["ms_per_slot_moved"]["warn"]
    assert not flags(rec, prev=None)[1]["ms_per_slot_moved"]["evaluated"]


def test_zero_keeper_slices_halt_not_crash():
    rec = healthy()
    rec["boost"]["last_slice_after_migrate_s"]["median"] = 0
    rec["boost"]["slices"]["median"] = 0
    halt, _ = flags(rec)
    assert halt["boost_last_slice_early"]["halt"] and halt["boost_budget_or_slices_changed"]["halt"]


# ---------------------------------------------------------------------------------------------
# end to end on the constructed chain (real RpcClient, real stages, real file output)
# ---------------------------------------------------------------------------------------------
def run_main(tmp_path, chain, extra=(), url="https://rpc.test/?k=SECRET", history=(), pins=None):
    out = tmp_path / "sub" / "daily.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if history:
        out.write_text("".join(json.dumps(h) + "\n" for h in history))
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(json.dumps(pins or TEST_PINS))
    rc = m.main(["--rpc-url", url, "--out", str(out), "--pins", str(pins_path), "--n", "10", "--min-interval", "0", *extra], client=client_for(chain.node()), now=NOW)
    return rc, out


def test_end_to_end_healthy_run(tmp_path, capsys):
    chain = Chain(n=10)
    rc, out = run_main(tmp_path, chain)
    assert rc == 0
    text = out.read_text()
    rec = json.loads(text)
    assert text.count("\n") == 1 and rec["schema"] == m.SCHEMA and rec["status"] == "ok" and rec["errors"] == []
    g = rec["graduations"]
    assert g["n"] == 10 and g["mayhem"] == {"true": 2, "false": 8, "unknown": 0} and g["quote"] == {"wsol": 10, "other": 0}
    assert g["boost_denominator"] == {"n_nonmayhem_wsol": 8, "n_init_boost": 8, "share": 1.0} and g["synthetic"]["n_checked"] == 10 and g["synthetic"]["n_synthetic"] == 0
    assert g["patterns"] == {"mayhem=0,quote=wsol,init_boost=1,synthetic=0": 8, "mayhem=1,quote=wsol,init_boost=0,synthetic=0": 2}
    b = rec["boost"]
    assert b["n_profiled"] == 8 and b["slices"]["median"] == 29 and b["last_slice_after_migrate_s"]["median"] == 342 and b["last_slice_verified"] == 8
    assert b["budget_sol"]["median"] == pytest.approx(17.586, abs=1e-3) and b["sol_total"]["median"] == b["budget_sol"]["median"]
    assert rec["pumpswap_trade_mix"]["v2_share"] == 0.5
    assert rec["halt"]["any"] is False and rec["halt"]["flags"]["pins_changed"]["halt"] is False
    assert rec["halt"]["n_halt_not_evaluated"] == 0 and rec["halt"]["all_halt_evaluated"] is True  # the six halt rules, as before
    # n_not_evaluated / all_evaluated also count the three change-watch rules: day one has no docs baseline, and 10 graduations are too few for the USDC rate
    assert rec["halt"]["watch_rules_not_evaluated"] == ["docs_changed", "usdc_boost_regime"] and rec["halt"]["n_not_evaluated"] == 2 and rec["halt"]["all_evaluated"] is False
    assert rec["rpc"]["calls"] < 400 and rec["rpc"]["cap"] == 400
    # privacy: no mint, pool, curve or signature ids, and no URL query (key) in the record
    for ident in chain.all_ids + ["SECRET", "?k="]:
        assert ident not in text, ident
    assert rec["rpc_host"] == "rpc.test"
    summary = capsys.readouterr().out
    assert "HALT: none" in summary and "InitBoost 8/8" in summary
    # a second run reads ms/slot from the file and warns when it moves
    node = chain.node()  # same chain, perf samples now 200 ms/slot
    node.handlers["getRecentPerformanceSamples"] = lambda p: [{"samplePeriodSecs": 60, "numSlots": 300, "slot": 1}] * 30
    rc = m.main(["--rpc-url", "https://rpc.test", "--out", str(out), "--n", "10", "--min-interval", "0"], client=client_for(node), now=NOW)
    second = json.loads(out.read_text().splitlines()[1])
    assert rc == 0 and second["prev_ms_per_slot"] == rec["slot_time"]["ms_per_slot_median"] and second["warn"]["flags"]["ms_per_slot_moved"]["warn"] is True
    assert second["docs_watch"]["prev_sha"] == rec["docs_watch"]["latest_sha"]  # the second run's docs baseline is the first run's head


def test_end_to_end_synthetic_four_of_ten_and_changed_config_halt(tmp_path):
    chain = Chain(n=10, synthetic=(2, 3, 4, 5))
    rc, out = run_main(tmp_path, chain)
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["halt"]["flags"]["synthetic_share_high"]["halt"] and rec["halt"]["any"]
    assert rec["graduations"]["synthetic"]["n_synthetic"] == 4 and rec["graduations"]["synthetic"]["n_mint_match"] == 4
    chain = Chain(n=10, config=config_result(flip_global_config))
    rc, out = run_main(tmp_path / "b", chain)
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["halt"]["flags"]["pins_changed"]["halt"] and "pumpswap_global_config" in rec["halt"]["flags"]["pins_changed"]["reason"]


def test_end_to_end_boost_share_seven_of_ten(tmp_path):
    chain = Chain(n=10, mayhem=(), no_boost=(0, 1, 2))  # 3 non-mayhem WSOL graduations without InitBoost
    rc, out = run_main(tmp_path, chain)
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["graduations"]["boost_denominator"]["n_init_boost"] == 7 and rec["halt"]["flags"]["boost_share_low"]["halt"]


def test_end_to_end_boost_off_world_halts_and_timing_rules_are_not_evaluated(tmp_path, capsys):
    # BOOST switched off: no InitBoost anywhere, so nothing is profiled and only the share rule can see it. Most
    # graduations are mayhem here, so the non-mayhem denominator (2) is below 5 and the all-WSOL fallback must catch it.
    chain = Chain(n=10, mayhem=tuple(range(8)), no_boost=(8, 9))
    rc, out = run_main(tmp_path, chain)
    rec = json.loads(out.read_text())
    f = rec["halt"]["flags"]
    assert rc == 0 and rec["graduations"]["boost_denominator"]["n_nonmayhem_wsol"] == 2 and rec["graduations"]["wsol_all"] == {"n": 10, "n_init_boost": 0}
    assert f["boost_share_low"]["halt"] and f["boost_share_low"]["evaluated"] and "fallback" in f["boost_share_low"]["reason"]
    assert rec["boost"]["n_profiled"] == 0
    assert not f["boost_last_slice_early"]["evaluated"] and not f["boost_budget_or_slices_changed"]["evaluated"]
    assert rec["halt"]["any"] and rec["halt"]["n_halt_not_evaluated"] == 2 and rec["halt"]["all_halt_evaluated"] is False
    assert rec["halt"]["n_not_evaluated"] == 4 and rec["halt"]["all_evaluated"] is False  # + docs baseline and the too-small USDC sample
    summary = capsys.readouterr().out
    assert "HALT boost_share_low" in summary and "(4 rules not evaluated)" in summary


def test_end_to_end_mayhem_stage_failure_falls_back_to_all_wsol(tmp_path):
    rc, out = run_main(tmp_path, Chain(n=10, fail_pools=True))  # Pool accounts unreadable; BOOST itself healthy (8 of 10)
    rec = json.loads(out.read_text())
    g, h = rec["graduations"], rec["halt"]["flags"]["boost_share_low"]
    assert rc == 0 and g["mayhem"] == {"true": 0, "false": 0, "unknown": 10} and g["mayhem_stage_failed"] is True
    assert any(e.startswith("graduations.mayhem") for e in rec["errors"]) and rec["status"] == "partial" and rec["warn"]["flags"]["stage_errors"]["warn"]
    assert h["evaluated"] and not h["halt"] and "mayhem stage failed" in h["reason"]
    # the same failure with BOOST off must still halt: without the fallback this was an all-clear
    rc, out = run_main(tmp_path / "off", Chain(n=10, fail_pools=True, mayhem=(), no_boost=tuple(range(10))))
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["halt"]["flags"]["boost_share_low"]["halt"] and rec["halt"]["any"]


def test_summary_and_record_say_when_rules_were_not_evaluated(tmp_path, capsys):
    rc, out = run_main(tmp_path, Chain(n=4), extra=("--n", "4"))  # 4 graduations: share, timing, size and synthetic rules all lack a sample
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["halt"]["any"] is False and rec["halt"]["n_halt_not_evaluated"] == 4 and rec["halt"]["all_halt_evaluated"] is False
    assert rec["halt"]["n_not_evaluated"] == 6 and rec["halt"]["all_evaluated"] is False  # + docs baseline and the too-small USDC sample
    out_text = capsys.readouterr().out
    assert "HALT: none (6 rules not evaluated)" in out_text and "NOT EVALUATED docs_changed" in out_text and "NOT EVALUATED usdc_boost_regime" in out_text


def test_end_to_end_early_last_slice_halts(tmp_path):
    chain = Chain(n=10, last_slice_s=300)
    rc, out = run_main(tmp_path, chain)
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["boost"]["last_slice_after_migrate_s"]["median"] == 300 and rec["halt"]["flags"]["boost_last_slice_early"]["halt"]


def test_one_failing_item_does_not_drop_its_stage(tmp_path):
    # graduation 3's migrate tx, graduation 2's completing tx and pool 4's newest BOOST slice each fail once
    chain = Chain(n=10, bad_txs={"mig3", "cmp2", "bst4_28"})
    rc, out = run_main(tmp_path, chain)
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["status"] == "partial"
    assert rec["item_errors"] == {"boost: RpcError": 1, "completion: RpcError": 1, "graduations: RpcError": 1}
    g = rec["graduations"]
    assert g["n"] == 9 and g["synthetic"]["n_checked"] == 8 and g["synthetic"]["n_completion_not_found"] == 1  # graduation 3 never sampled; graduation 2 has no completion
    b = rec["boost"]
    assert b["n_profiled"] == 6 and b["n_pools_skipped"] == 1 and b["slices"]["median"] == 29 and b["last_slice_verified"] == 6
    assert rec["pumpswap_trade_mix"]["n_txs"] == 40 and rec["warn"]["flags"]["stage_errors"]["warn"]
    assert "boost: RpcError: 1 item(s) skipped" in rec["errors"]


def test_call_cap_mid_stage_keeps_the_partial_results(tmp_path, capsys):
    chain = Chain(n=10)
    out = tmp_path / "d.jsonl"
    # 4 setup calls + sample page + 11 fetches + 1 mayhem = 17, then 2 calls per completion lookup: the cap lands inside that stage
    rc = m.main(["--out", str(out), "--n", "10", "--min-interval", "0"], client=client_for(chain.node(), max_calls=24), now=NOW)
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["rpc"]["calls"] == 24 and rec["rpc"]["cap"] == 24
    assert rec["graduations"]["n"] == 10 and rec["graduations"]["mayhem"]["false"] + rec["graduations"]["mayhem"]["true"] == 10
    assert 0 < rec["graduations"]["synthetic"]["n_checked"] < 10  # completions annotated before the cap are kept
    assert rec["item_errors"]["completion: call cap"] == 1 and rec["item_errors"]["boost: call cap"] == 1
    assert any(e.startswith("pumpswap_trade_mix") for e in rec["errors"])  # the stage that never got a call
    assert rec["status"] == "partial" and rec["accounts"] and rec["programs"]  # the cheap early stages are intact
    assert "pins: 6 unchanged" in capsys.readouterr().out


def test_end_to_end_unverified_last_slices_leave_the_timing_rule_not_evaluated(tmp_path):
    chain = Chain(n=10, slice_signer=m.b58encode(k(7)))  # every slice tx is signed by someone other than boost_authority
    rc, out = run_main(tmp_path, chain)
    rec = json.loads(out.read_text())
    f = rec["halt"]["flags"]["boost_last_slice_early"]
    assert rc == 0 and rec["boost"]["n_profiled"] == 8 and rec["boost"]["last_slice_unverified"] == 8 and rec["boost"]["last_slice_after_migrate_s"] is None
    assert f["halt"] is False and f["evaluated"] is False and "0 of 8" in f["reason"]
    assert rec["rpc"]["calls"] < 400


def test_stage_failure_is_data_not_a_crash(tmp_path):
    chain = Chain(n=10)
    node = chain.node()
    node.handlers["getRecentPerformanceSamples"] = lambda p: (_ for _ in ()).throw(KeyError("boom"))
    out2 = tmp_path / "x.jsonl"
    rc = m.main(["--out", str(out2), "--n", "10", "--min-interval", "0"], client=client_for(node), now=NOW)
    rec = json.loads(out2.read_text())
    assert rc == 0 and rec["status"] == "partial" and any("slot_time" in e for e in rec["errors"])


def test_unreachable_rpc_exits_2_and_writes_nothing(tmp_path):
    def boom(u, b, t):
        raise OSError("down")

    out = tmp_path / "d.jsonl"
    c = m.RpcClient("https://rpc.test", min_interval=0, max_retries=1, sleep=lambda s: None, transport=boom)
    assert m.main(["--out", str(out)], client=c, now=NOW) == 2
    assert not out.exists()


def test_rpc_host_never_carries_userinfo_path_or_query(tmp_path):
    assert m.rpc_host("https://api.mainnet-beta.solana.com") == "api.mainnet-beta.solana.com"
    assert m.rpc_host("https://uname77:pw99@rpc.test:8899/p/ath?k=SECRET") == "rpc.test:8899"
    assert m.rpc_host("https://uname77@[::1]:8899/") == "::1:8899"
    assert m.rpc_host("https://h:notaport/") == "unknown" and m.rpc_host("") == "unknown"
    rc, out = run_main(tmp_path, Chain(n=10), url="https://uname77:pw99@rpc.test:8899/?k=SECRET")
    text = out.read_text()
    assert rc == 0 and json.loads(text)["rpc_host"] == "rpc.test:8899"
    assert not any(s in text for s in ("uname77", "pw99", "SECRET"))


def test_halt_is_printed_before_a_failed_write_and_exit_is_1(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # --out under a regular file: mkdir/open raises OSError
    chain = Chain(n=10, config=config_result(flip_global_config))
    rc = m.main(["--out", str(blocker / "d.jsonl"), "--n", "10", "--min-interval", "0"], client=client_for(chain.node()), now=NOW)
    cap = capsys.readouterr()
    assert rc == 1 and "HALT pins_changed" in cap.out and "WRITE FAILED" in cap.err


def test_helius_urls_are_refused_before_any_call(tmp_path):
    node = Node({})
    assert m.main(["--rpc-url", "https://mainnet.helius-rpc.com/?api-key=abc", "--out", str(tmp_path / "d.jsonl")], client=client_for(node)) == 64
    assert node.methods == []


def test_write_pins_reproduces_committed_pins(tmp_path, monkeypatch):
    by_addr = {addr: name for name, addr in PROGRAMDATA_ADDR.items()}
    node = Node({
        "getEpochInfo": lambda p: fx("epoch_info.json"), "getMultipleAccounts": lambda p: fx("programdata.json") if p[1].get("dataSlice") else config_result(), "getBlockTime": lambda p: 0,
        "getAccountInfo": lambda p: {"context": {"slot": 1}, "value": acct(programdata_bytes(by_addr[p[0]]))},
    })
    github_calls: list = []
    monkeypatch.setattr(m, "urllib_get", fake_github(calls=github_calls))
    path = tmp_path / "pins.json"
    assert m.main(["--write-pins", str(path), "--min-interval", "0"], client=client_for(node), now=NOW) == 0
    new = json.loads(path.read_text())
    assert new["accounts"] == PINS["accounts"] and {k_: v["deploy_slot"] for k_, v in new["programs"].items()} == {k_: v["deploy_slot"] for k_, v in PINS["programs"].items()}
    assert new["boost"] == PINS["boost"] == {"budget_sol": 17.585, "slices": 29}
    assert new["programs"] == TEST_PINS["programs"]  # deploy slot, block time, and the sha256 / length of the whole programdata account
    assert github_calls == [] and node.methods.count("getAccountInfo") == 3  # pinning reads chain state only: no docs call


def test_last_ms_per_slot_reads_the_tail_and_skips_garbage(tmp_path):
    p = tmp_path / "d.jsonl"
    assert m.last_ms_per_slot(p) is None
    p.write_text(json.dumps({"slot_time": {"ms_per_slot_median": 300.0}}) + "\n" + json.dumps({"slot_time": {"ms_per_slot_median": None}}) + "\nnot json\n")
    assert m.last_ms_per_slot(p) == 300.0


def test_dist_helper():
    assert m.dist([3, None, 1, 2]) == {"n": 3, "min": 1, "median": 2, "max": 3}
    assert m.dist([]) is None and m.share(1, 0) is None


# =============================================================================================
# change watch (T1 of the 2026-10-08 edge scan; plan: EXP/EXP-023-usdc-boost-tripwire-plan.md). Fixture tests, no network.
# =============================================================================================
HEAD = fx("docs_commits.json")[0]["sha"]


def healthy_hashes():
    rec = healthy()
    for name in rec["programs"]:
        data = programdata_bytes(name)
        rec["programs"][name].update(sha256=hashlib.sha256(data).hexdigest(), data_len=len(data))
    return rec


def watch(rec, history=(), pins=TEST_PINS):
    return m.compute_flags(rec, pins, 268.0, history=history)


# ---- 1. program fingerprints ----------------------------------------------------------------
def test_program_hashes_are_the_whole_programdata_account():
    node = Chain(n=2).node()
    got = m.stage_program_hashes(client_for(node), PROGRAMDATA_ADDR)
    assert set(got) == {"pump", "pumpswap", "fees"} and node.methods == ["getAccountInfo"] * 3
    for name, h in got.items():
        data = programdata_bytes(name)
        assert h == {"sha256": hashlib.sha256(data).hexdigest(), "data_len": len(data)}
    assert got["pump"]["sha256"] != got["pumpswap"]["sha256"]


def test_program_hash_read_failure_is_unread_not_a_change():
    node = Chain(n=2).node()
    node.handlers["getAccountInfo"] = lambda p: (_ for _ in ()).throw(NodeError())
    errs = Counter()
    assert m.stage_program_hashes(client_for(node), PROGRAMDATA_ADDR, errs) == {} and errs["program_hashes: RpcError"] == 3
    rec = healthy()  # no hash read at all
    _, w = watch(rec)
    assert not w["program_changed"]["evaluated"] and not w["program_changed"]["warn"] and "unread" in w["program_changed"]["reason"]


def test_program_changed_warns_with_old_and_new_values_and_does_not_duplicate_the_slot_halt():
    rec = healthy_hashes()
    h, w = watch(rec)
    assert w["program_changed"] == {"warn": False, "evaluated": True, "reason": "3 programdata hashes unchanged"} and not any(v["halt"] for v in h.values())
    # the bytes changed, the deploy slot did not: a WARN with old and new values, no halt
    rec["programs"]["pump"].update(sha256="ab" * 32, data_len=999)
    h, w = watch(rec)
    pc = w["program_changed"]
    ch = pc["changes"][0]
    assert pc["warn"] and pc["evaluated"] and not h["pins_changed"]["halt"] and len(pc["changes"]) == 1
    assert ch["program"] == "pump" and ch["old_sha256"] == TEST_PINS["programs"]["pump"]["sha256"] and ch["new_sha256"] == "ab" * 32
    assert ch["old_data_len"] == TEST_PINS["programs"]["pump"]["data_len"] and ch["new_data_len"] == 999
    assert "program_pump" in pc["reason"] and ("ab" * 8) in pc["reason"] and TEST_PINS["programs"]["pump"]["sha256"][:16] in pc["reason"] and "pins_changed" not in pc["reason"]
    # slot and bytes both changed: the one existing HALT stays in pins_changed, and the WARN only quotes the slot
    rec["programs"]["pump"]["deploy_slot"] += 5
    h, w = watch(rec)
    assert [k_ for k_, v in h.items() if v["halt"]] == ["pins_changed"] and "program_pump deploy_slot" in h["pins_changed"]["reason"]
    assert w["program_changed"]["warn"] and "halts in pins_changed" in w["program_changed"]["reason"]
    # the slot moved but the same bytes were redeployed: the existing halt, and no WARN
    rec = healthy_hashes()
    rec["programs"]["fees"]["deploy_slot"] += 1
    h, w = watch(rec)
    assert h["pins_changed"]["halt"] and not w["program_changed"]["warn"] and w["program_changed"]["evaluated"]


def test_program_changed_needs_a_pin_and_a_read():
    pins = copy.deepcopy(TEST_PINS)
    for p in pins["programs"].values():
        p["sha256"] = None
    _, w = watch(healthy_hashes(), pins=pins)
    assert not w["program_changed"]["evaluated"] and not w["program_changed"]["warn"] and "unpinned" in w["program_changed"]["reason"]
    rec = healthy_hashes()
    rec["programs"]["fees"].pop("sha256")  # one program unread: the others still compare, and the reason names the gap
    _, w = watch(rec)
    assert w["program_changed"]["evaluated"] and not w["program_changed"]["warn"] and "unread: ['fees']" in w["program_changed"]["reason"]


def test_committed_program_hash_pins_are_well_formed_when_present():
    # The hash pins are added through the --write-pins review path; until then program_changed reports "unpinned" (not a change).
    assert set(PINS["programs"]) == {"pump", "pumpswap", "fees"}
    for p in PINS["programs"].values():
        assert p.get("sha256") is None or (re.fullmatch(r"[0-9a-f]{64}", p["sha256"]) and p["data_len"] > 45)


def test_end_to_end_program_hash_change_is_a_warn_not_a_halt(tmp_path):
    rc, out = run_main(tmp_path, Chain(n=10, tweak={"pumpswap": b"\x01new-instruction"}))
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["warn"]["flags"]["program_changed"]["warn"] and rec["halt"]["any"] is False and rec["halt"]["flags"]["pins_changed"]["halt"] is False
    assert "program_pumpswap" in rec["warn"]["flags"]["program_changed"]["reason"] and rec["programs"]["pumpswap"]["data_len"] == len(programdata_bytes("pumpswap")) + 16
    assert rec["programs"]["pump"]["sha256"] == TEST_PINS["programs"]["pump"]["sha256"]


# ---- 2. docs watch --------------------------------------------------------------------------
def test_docs_commit_list_parses_and_rejects_garbage():
    commits = m.parse_docs_commits(json.dumps(fx("docs_commits.json")).encode())
    assert len(commits) == 30 and commits[0]["sha"] == HEAD and commits[0]["date"] == "2026-10-07T20:11:31Z" and commits[0]["title"].startswith("docs: v3 / v2 trades")
    assert all(len(c["sha"]) == 40 and c["title"] and "\n" not in c["title"] for c in commits)
    for bad in (b"[]", b"{}", b"not json", b'[{"sha": "zz"}]', b'[{"commit": {}}]'):
        with pytest.raises(ValueError):
            m.parse_docs_commits(bad)


def test_docs_first_run_is_a_baseline_and_not_evaluated():
    calls: list = []
    dw = m.stage_docs(fake_github(calls=calls), None)
    assert dw["ok"] and dw["calls"] == 1 and dw["latest_sha"] == HEAD and dw["n_new"] == 0 and dw["new_commits"] == []
    f = m.compute_watch_flags({"docs_watch": dw}, TEST_PINS, [])["docs_changed"]
    assert not f["evaluated"] and not f["warn"] and f"baseline {HEAD[:7]}" in f["reason"]
    assert calls == [m.DOCS_API.format(repo="pump-fun/pump-public-docs", n=30)] and calls[0].startswith("https://api.github.com/repos/pump-fun/pump-public-docs/commits")


def test_docs_unchanged_head_is_evaluated_and_quiet():
    dw = m.stage_docs(fake_github(), HEAD)
    f = m.compute_watch_flags({"docs_watch": dw}, TEST_PINS, [])["docs_changed"]
    assert dw["ok"] and dw["n_new"] == 0 and f["evaluated"] and not f["warn"] and "unchanged" in f["reason"]


def test_docs_new_commits_warn_and_list_the_titles():
    dw = m.stage_docs(fake_github((200, docs_body(n_new=2))), HEAD)  # the last run saw HEAD; two commits have landed since
    f = m.compute_watch_flags({"docs_watch": dw}, TEST_PINS, [])["docs_changed"]
    assert dw["n_new"] == 2 and not dw["window_overflow"] and dw["latest_sha"] == f"{0xdead00 + 1:040x}"
    assert [c["title"] for c in dw["new_commits"]] == ["docs: constructed change 1", "docs: constructed change 0"]
    assert f["warn"] and f["evaluated"] and "2 new commit(s)" in f["reason"] and "docs: constructed change 1" in f["reason"] and "docs: constructed change 0" in f["reason"]
    assert HEAD[:7] in f["reason"]  # "since <previous head>"


def test_docs_previous_head_outside_the_window_is_flagged_not_guessed():
    dw = m.stage_docs(fake_github(), "c" * 40)  # a head we have never seen in the newest 30: at least 30 are new
    f = m.compute_watch_flags({"docs_watch": dw}, TEST_PINS, [])["docs_changed"]
    assert dw["window_overflow"] and dw["n_new"] == 30 and len(dw["new_commits"]) == m.DOCS_TITLES_SHOWN
    assert f["warn"] and "at least 30" in f["reason"] and "(+20 more)" in f["reason"]


@pytest.mark.parametrize("responses, calls, why", [
    ((OSError("down"),), 2, "network OSError"),  # one retry, then give up
    (((500, b""),), 2, "http 500"),
    (((403, b'{"message": "rate limit"}'),), 1, "rate limited"),  # not retried
    (((404, b""),), 1, "http 404"),
    (((200, b"garbage"),), 1, "undecodable"),
    (((200, b"[]"),), 1, "undecodable"),
])
def test_docs_unreachable_is_not_evaluated_and_never_raises(responses, calls, why):
    seen: list = []
    dw = m.stage_docs(fake_github(*responses, calls=seen), HEAD, sleep=lambda s: None)
    f = m.compute_watch_flags({"docs_watch": dw}, TEST_PINS, [])["docs_changed"]
    assert not dw["ok"] and dw["calls"] == calls == len(seen) <= m.DOCS_MAX_CALLS and why in dw["error"]
    assert not f["evaluated"] and not f["warn"] and why in f["reason"]


def test_docs_one_retry_after_a_server_error_recovers():
    seen: list = []
    dw = m.stage_docs(fake_github((502, b""), (200, docs_body()), calls=seen), HEAD, sleep=lambda s: None)
    assert dw["ok"] and dw["calls"] == 2 and dw["latest_sha"] == HEAD


def test_docs_baseline_comes_from_the_newest_record_that_has_a_head():
    hist = [{"docs_watch": {"latest_sha": "a" * 40}}, {"docs_watch": {"latest_sha": "b" * 40}}, {"docs_watch": {"latest_sha": None, "ok": False}}, {"no": "docs"}]
    assert m.last_docs_sha(hist) == "b" * 40 and m.last_docs_sha([]) is None and m.last_docs_sha([{"docs_watch": {"latest_sha": "short"}}]) is None


def test_docs_request_is_unauthenticated_and_no_token_is_read(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        headers = {"x": "y"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[]"

    def fake_urlopen(req, timeout=None):
        seen.update(url=req.full_url, headers={k_.lower(): v for k_, v in req.header_items()}, timeout=timeout)
        return Resp()

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    assert REAL_URLLIB_GET("https://api.github.com/repos/x/y/commits", 5.0)[0] == 200
    assert seen["url"].startswith("https://api.github.com/") and set(seen["headers"]) <= {"accept", "user-agent"} and seen["timeout"] == 5.0
    src = Path(m.__file__).read_text()
    assert "environ" not in src and "getenv" not in src and "Authorization" not in src.replace("no Authorization header", "")


def test_end_to_end_docs_change_warns_and_the_second_run_sees_only_new_commits(tmp_path, monkeypatch, capsys):
    chain = Chain(n=10)
    rc, out = run_main(tmp_path, chain)  # day 1: baseline
    first = json.loads(out.read_text())
    assert rc == 0 and first["docs_watch"]["latest_sha"] == HEAD and first["docs_watch"]["calls"] == 1 and not first["warn"]["flags"]["docs_changed"]["evaluated"]
    monkeypatch.setattr(m, "urllib_get", fake_github((200, docs_body(n_new=1))))
    rc = m.main(["--out", str(out), "--pins", str(tmp_path / "pins.json"), "--n", "10", "--min-interval", "0"], client=client_for(chain.node()), now=NOW + 86400)
    second = json.loads(out.read_text().splitlines()[1])
    capsys.readouterr()
    f = second["warn"]["flags"]["docs_changed"]
    assert rc == 0 and f["warn"] and f["evaluated"] and "docs: constructed change 0" in f["reason"] and second["warn"]["any"] is True and second["halt"]["any"] is False
    rc = m.main(["--out", str(out), "--pins", str(tmp_path / "pins.json"), "--n", "10", "--min-interval", "0"], client=client_for(chain.node()), now=NOW + 2 * 86400)
    third = json.loads(out.read_text().splitlines()[2])
    assert rc == 0 and third["warn"]["flags"]["docs_changed"]["evaluated"] and not third["warn"]["flags"]["docs_changed"]["warn"]
    assert "docs pump-fun/pump-public-docs" in capsys.readouterr().out


def test_end_to_end_docs_unreachable_is_not_evaluated_and_does_not_fail_the_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(m, "urllib_get", fake_github(OSError("github down")))
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    rc, out = run_main(tmp_path, Chain(n=10))
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["status"] == "ok" and rec["docs_watch"]["ok"] is False and rec["docs_watch"]["calls"] == 2
    assert not rec["warn"]["flags"]["docs_changed"]["evaluated"] and "docs_changed" in rec["halt"]["watch_rules_not_evaluated"]
    assert "NOT EVALUATED docs_changed: docs repo not read: network OSError" in capsys.readouterr().out


def test_no_docs_flag_makes_no_github_call(tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(m, "urllib_get", fake_github(calls=seen))
    rc, out = run_main(tmp_path, Chain(n=10), extra=("--no-docs",))
    rec = json.loads(out.read_text())
    assert rc == 0 and seen == [] and "docs_watch" not in rec and not rec["warn"]["flags"]["docs_changed"]["evaluated"]


# ---- 3. BOOST quote mix ---------------------------------------------------------------------
def test_quote_mix_counts_boost_graduations_by_quote_class(tmp_path):
    # 30 graduations; the main sample takes the newest 10, the extension reads the 20 older. USDC: 3, 4, 5 (no BOOST), 20. Pump-token quote: 6, 7 (no BOOST).
    chain = Chain(n=30, mayhem=(), no_boost=(5, 7), usdc=(3, 4, 5, 20), other=(6, 7))
    rc, out = run_main(tmp_path, chain)
    text = out.read_text()
    rec = json.loads(text)
    qm = rec["quote_mix"]
    assert rc == 0 and rec["errors"] == [] and rec["graduations"]["n"] == 10
    assert qm["n"] == 30 and qm["n_main"] == 10 and qm["n_extra"] == 20 and qm["span_s"] == 29 * 40
    assert qm["by_quote"] == {"wsol": {"n": 24, "n_init_boost": 24}, "usdc": {"n": 4, "n_init_boost": 3}, "other": {"n": 2, "n_init_boost": 1}}
    assert qm["n_other_quote_mints"] == 1  # both pump-token pools share one quote mint
    assert qm["usdc_boost_per_day_est"] == round(3 * 86400 / (29 * 40), 1)
    assert "slot-0 buy/V" in qm["not_recorded"]
    for ident in chain.all_ids + [m.USDC_MINT, m.b58encode(PUMP_QUOTE), "SECRET"]:  # counts only: no mint of any kind in the record
        assert ident not in text, ident


def test_quote_mix_extension_costs_one_page_and_one_fetch_per_graduation(tmp_path):
    with_ext = json.loads(run_main(tmp_path / "a", Chain(n=30))[1].read_text())
    no_ext = json.loads(run_main(tmp_path / "b", Chain(n=30), extra=("--quote-mix-extra", "0"))[1].read_text())
    assert no_ext["quote_mix"]["n"] == 10 and no_ext["quote_mix"]["n_extra"] == 0 and with_ext["quote_mix"]["n"] == 30
    assert with_ext["rpc"]["calls"] - no_ext["rpc"]["calls"] == 1 + 20  # one signature page and one getTransaction per extra graduation
    assert with_ext["rpc"]["by_method"]["getAccountInfo"] == no_ext["rpc"]["by_method"]["getAccountInfo"] == 3
    assert with_ext["graduations"] == no_ext["graduations"] and with_ext["boost"] == no_ext["boost"]  # the halt-rule inputs do not change


def test_quote_mix_rate_is_not_estimated_from_a_small_or_short_sample():
    def g(i, usdc, bt):
        return {"quote_mint": m.USDC_MINT if usdc else m.WSOL_MINT, "quote_wsol": not usdc, "init_boost": True, "block_time": bt}

    small = m.summarize_quote_mix([g(i, True, 1_000 + 100 * i) for i in range(19)], 19, 0)
    assert small["usdc_boost_per_day_est"] is None and small["by_quote"]["usdc"] == {"n": 19, "n_init_boost": 19}
    short = m.summarize_quote_mix([g(i, True, 1_000 + 10 * i) for i in range(20)], 20, 0)  # 190 s span
    assert short["usdc_boost_per_day_est"] is None
    ok = m.summarize_quote_mix([g(i, i < 2, 1_000 + 20 * i) for i in range(20)], 20, 0)  # 2 USDC BOOST in 380 s
    assert ok["usdc_boost_per_day_est"] == round(2 * 86400 / 380, 1)
    empty = m.summarize_quote_mix([], 0, 40)
    assert empty["n"] == 0 and empty["usdc_boost_per_day_est"] is None and empty["span_s"] is None


def test_quote_mix_failed_extension_keeps_the_main_sample(tmp_path):
    chain = Chain(n=30)
    bad = {f"mig{i}" for i in range(10, 30)}  # every extension fetch fails
    chain.bad_txs = bad
    rc, out = run_main(tmp_path, chain)
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["quote_mix"]["n"] == 10 and rec["quote_mix"]["n_extra"] == 0 and rec["item_errors"]["quote_mix: RpcError"] == 20
    assert not rec["warn"]["flags"]["usdc_boost_regime"]["evaluated"] and rec["graduations"]["n"] == 10


# ---- 4. USDC-BOOST tripwire: 10 a day on 5 consecutive daily runs --------------------------------
def day_rec(est, days_ago=0, quote_mix=True):
    d = TODAY - timedelta(days=days_ago)
    rec = {"schema": m.SCHEMA, "run_utc": f"{d.isoformat()}T06:41:00Z"}
    if quote_mix:
        rec["quote_mix"] = {"n": 60, "span_s": 4000, "by_quote": {"usdc": {"n": 3, "n_init_boost": 3}}, "usdc_boost_per_day_est": est}
    return rec


def regime(est, history):
    return m.compute_watch_flags(day_rec(est), TEST_PINS, history)["usdc_boost_regime"]


def test_tripwire_fires_on_five_consecutive_daily_runs_at_ten_a_day():
    f = regime(12.0, [day_rec(11.0, i) for i in (1, 2, 3, 4)])
    assert f["warn"] and f["evaluated"] and f["streak_days"] == 5 and "5 of 5 consecutive daily runs" in f["reason"]
    assert [d for d, _ in f["estimates_newest_first"]] == [(TODAY - timedelta(days=i)).isoformat() for i in range(5)]
    assert regime(12.0, [day_rec(11.0, i) for i in (1, 2, 3, 4, 5, 6, 7)])["warn"]  # a longer run still fires


def test_tripwire_does_not_fire_on_four_days_or_below_the_bar():
    f = regime(12.0, [day_rec(11.0, i) for i in (1, 2, 3)])
    assert not f["warn"] and f["evaluated"] and f["streak_days"] == 4 and "4 of 5" in f["reason"]
    assert regime(12.0, [])["streak_days"] == 1 and not regime(12.0, [])["warn"]  # a fresh history is evaluated, it simply cannot fire yet
    assert regime(10.0, [day_rec(10.0, i) for i in (1, 2, 3, 4)])["warn"]  # exactly 10 a day counts
    f = regime(9.9, [day_rec(50.0, i) for i in (1, 2, 3, 4)])
    assert not f["warn"] and f["streak_days"] == 0  # today is below the bar
    f = regime(12.0, [day_rec(50.0, 1), day_rec(50.0, 2), day_rec(9.9, 3), day_rec(50.0, 4)])
    assert not f["warn"] and f["streak_days"] == 3  # a day below the bar resets the count


def test_tripwire_resets_on_a_gap():
    f = regime(12.0, [day_rec(11.0, i) for i in (1, 2, 4, 5)])  # no run on day -3
    assert not f["warn"] and f["streak_days"] == 3
    assert regime(12.0, [day_rec(11.0, i) for i in (2, 3, 4, 5)])["streak_days"] == 1  # yesterday missing: only today counts
    assert regime(12.0, [day_rec(11.0, i) for i in (1, 2, 3, 4)])["warn"]  # the same days without the gap fire
    f = regime(12.0, [day_rec(11.0, 1), day_rec(11.0, 2), day_rec(None, 3), day_rec(11.0, 4), day_rec(11.0, 5)])
    assert not f["warn"] and f["streak_days"] == 3  # a run whose estimate was not evaluable counts as no evidence
    f = regime(12.0, [day_rec(11.0, 1), day_rec(11.0, 2), day_rec(0, 3, quote_mix=False), day_rec(11.0, 4), day_rec(11.0, 5)])
    assert not f["warn"] and f["streak_days"] == 3  # a record from before the quote mix existed is no evidence either


def test_tripwire_one_value_per_utc_day_and_order_does_not_matter():
    hist = [day_rec(11.0, i) for i in (1, 2, 3)] + [day_rec(80.0, 0)]  # an earlier run today is replaced by this run, not added to it
    f = regime(12.0, hist)
    assert not f["warn"] and f["streak_days"] == 4
    hist = [day_rec(11.0, i) for i in (1, 2, 3, 4)] + [day_rec(11.0, 2)]  # two runs on day -2: still one day
    assert regime(12.0, hist)["streak_days"] == 5
    hist = [day_rec(11.0, 1), day_rec(11.0, 2), day_rec(11.0, 3), day_rec(3.0, 3), day_rec(11.0, 4)]  # the later line of a date wins
    assert regime(12.0, hist)["streak_days"] == 3
    hist = [day_rec(11.0, i) for i in (1, 2, 3, 4)]
    random.Random(1).shuffle(hist)
    assert regime(12.0, hist)["warn"]
    assert regime(12.0, [day_rec(11.0, i) for i in (1, 2, 3)] + [day_rec(500.0, -1)])["streak_days"] == 4  # tomorrow's record (clock skew) is ignored
    assert regime(12.0, [day_rec(11.0, 1), {"run_utc": "garbage", "quote_mix": {"usdc_boost_per_day_est": 99}}])["streak_days"] == 2


def test_tripwire_is_not_evaluated_without_a_rate_this_run():
    rec = day_rec(None)
    f = m.compute_watch_flags(rec, TEST_PINS, [day_rec(11.0, i) for i in (1, 2, 3, 4)])["usdc_boost_regime"]
    assert not f["evaluated"] and not f["warn"] and "not evaluable" in f["reason"]
    assert not m.compute_watch_flags({"run_utc": rec["run_utc"]}, TEST_PINS, [])["usdc_boost_regime"]["evaluated"]


def test_end_to_end_tripwire_fires_and_prints_after_four_prior_days(tmp_path, capsys):
    chain = Chain(n=30, mayhem=(), usdc=(3, 12, 25))  # 3 USDC BOOST graduations in 1,160 s: well over 10 a day
    rc, out = run_main(tmp_path, chain, history=[day_rec(11.0, i) for i in (4, 3, 2, 1)])
    rec = json.loads(out.read_text().splitlines()[-1])
    f = rec["warn"]["flags"]["usdc_boost_regime"]
    assert rc == 0 and f["warn"] and f["streak_days"] == 5 and rec["warn"]["any"] is True and rec["halt"]["any"] is False
    summary = capsys.readouterr().out
    assert "WARN usdc_boost_regime: USDC-quoted BOOST graduations: 3 in 30 sampled" in summary and "HALT: none" in summary and "quote mix: 30 graduations" in summary
    # the same history with a missing day does not fire
    rc, out = run_main(tmp_path / "gap", chain, history=[day_rec(11.0, i) for i in (5, 3, 2, 1)])
    f = json.loads(out.read_text().splitlines()[-1])["warn"]["flags"]["usdc_boost_regime"]
    assert rc == 0 and f["evaluated"] and not f["warn"] and f["streak_days"] == 4  # today and days -1..-3; day -4 is missing
    assert "WARN usdc_boost_regime" not in capsys.readouterr().out


def test_end_to_end_no_usdc_pools_means_zero_rate_and_no_warn(tmp_path):
    rc, out = run_main(tmp_path, Chain(n=30))
    rec = json.loads(out.read_text())
    f = rec["warn"]["flags"]["usdc_boost_regime"]
    assert rc == 0 and rec["quote_mix"]["usdc_boost_per_day_est"] == 0.0 and f["evaluated"] and not f["warn"] and f["streak_days"] == 0


# ---- 5. output: backward compatible, new rules counted ----------------------------------------
def test_n_not_evaluated_counts_the_watch_rules_and_the_halt_only_counts_are_kept(tmp_path, capsys):
    chain = Chain(n=30)
    rc, out = run_main(tmp_path, chain)  # day 1: no docs baseline
    first = json.loads(out.read_text())
    assert rc == 0 and first["halt"]["watch_rules_not_evaluated"] == ["docs_changed"] and first["halt"]["n_not_evaluated"] == 1 and first["halt"]["all_evaluated"] is False
    assert first["halt"]["n_halt_not_evaluated"] == 0 and first["halt"]["all_halt_evaluated"] is True
    assert "HALT: none (1 rules not evaluated)" in capsys.readouterr().out
    rc = m.main(["--out", str(out), "--pins", str(tmp_path / "pins.json"), "--n", "10", "--min-interval", "0"], client=client_for(chain.node()), now=NOW + 86400)
    second = json.loads(out.read_text().splitlines()[1])
    assert rc == 0 and second["halt"]["watch_rules_not_evaluated"] == [] and second["halt"]["n_not_evaluated"] == 0 and second["halt"]["all_evaluated"] is True
    assert "HALT: none\n" in capsys.readouterr().out + "\n"
    # the earlier keys are all still there, with the earlier meaning
    for key in ("any", "n_not_evaluated", "all_evaluated", "flags"):
        assert key in second["halt"]
    assert set(second["halt"]["flags"]) == {"pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed", "synthetic_share_high"}
    for key in ("any", "flags"):
        assert key in second["warn"]
    assert {"ms_per_slot_moved", "graduation_sample_short", "rules_not_evaluated"} <= set(second["warn"]["flags"]) and {"program_changed", "docs_changed", "usdc_boost_regime"} <= set(second["warn"]["flags"])


def test_summary_lists_the_new_rules(tmp_path, capsys):
    rc, out = run_main(tmp_path, Chain(n=30, mayhem=(), usdc=(3,), other=(6,)))
    summary = capsys.readouterr().out
    assert rc == 0
    assert "program fingerprints: pump " in summary and "pumpswap " in summary and "fees " in summary
    assert "quote mix: 30 graduations (10 + 20 older) over 1160 s; InitBoost/total wsol 28/28, usdc 1/1, other 1/1 (1 quote mints)" in summary
    assert "docs pump-fun/pump-public-docs: head 8cda1fa" in summary and "1 http call(s)" in summary


def test_read_tail_records_skips_garbage_and_a_cut_first_line(tmp_path):
    p = tmp_path / "d.jsonl"
    assert m.read_tail_records(p) == []
    p.write_text('{"a": 1}\nnot json\n[1, 2]\n{"b": 2}\n')
    assert m.read_tail_records(p) == [{"a": 1}, {"b": 2}]
    assert m.read_tail_records(p, max_bytes=12) == [{"b": 2}]  # the cut first line fails to parse and is skipped


def test_watch_stages_run_after_the_halt_rule_stages(tmp_path):
    # a tight cap starves the watch, never a halt rule: with the cap at 24 the halt inputs are as before (test above) and the watch reports errors
    chain = Chain(n=10)
    out = tmp_path / "d.jsonl"
    rc = m.main(["--out", str(out), "--n", "10", "--min-interval", "0"], client=client_for(chain.node(), max_calls=24), now=NOW)
    rec = json.loads(out.read_text())
    assert rc == 0 and rec["rpc"]["calls"] == 24 and rec["graduations"]["n"] == 10
    assert rec["item_errors"]["program_hashes: call cap"] == 1 and rec["item_errors"]["quote_mix: call cap"] == 1
    assert not rec["warn"]["flags"]["program_changed"]["evaluated"] and not rec["warn"]["flags"]["usdc_boost_regime"]["evaluated"]
