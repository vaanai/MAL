"""Offline tests for tools/h5_upgrade_dryrun.py (fake RPC, no network)."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from observe import trade_decode as td
from tools import h5_upgrade_dryrun as d
from tools import pumpswap_tx as tx

TIER0 = (2, 93, 30)
TIER1 = (20, 5, 95)
TIERS = [(0, TIER0), (420 * 10**9, TIER1), (1470 * 10**9, (20, 5, 90))]
SUPPLY = 10**15
V = 17_584_505_288
B_RES = 4 * 10**14  # base vault; mcap = (80e9 + V) * 1e15 / 4e14 ~ 244 SOL: tier 0
Q_VAULT = 80 * 10**9


def fee_config_bytes() -> bytes:
    out = bytearray(8 + 1 + 32) + struct.pack("<3Q", 25, 5, 0)
    out += struct.pack("<I", len(TIERS))
    for thr, bps in TIERS:
        out += struct.pack("<QQ", thr & (2**64 - 1), thr >> 64) + struct.pack("<3Q", *bps)
    out += struct.pack("<I", 1) + struct.pack("<QQ", 0, 0) + struct.pack("<3Q", 20, 5, 5)
    return bytes(out) + bytes(4097 - len(out))


FEE_CFG = fee_config_bytes()
PINS = {"accounts": {"pumpswap_fee_config": {"address": str(tx.fee_config()), "len": 4097, "sha256": hashlib.sha256(FEE_CFG).hexdigest()}}}
OWNER = str(tx.PUMPSWAP_PROGRAM)


def acct(data: bytes, owner: str) -> dict:
    return {"data": [base64.b64encode(data).decode(), "base64"], "owner": owner, "lamports": 1}


def make_pool(i: int, *, q_vault: int = Q_VAULT, b_res: int = B_RES) -> dict:
    mint = Pubkey.from_bytes(bytes([i + 1]) * 32)
    pool = tx.canonical_pool(mint)
    bv, qv = Pubkey.from_bytes(bytes([100 + i]) * 32), Pubkey.from_bytes(bytes([150 + i]) * 32)
    data = bytearray(8 + 1 + 2)
    for pk in (Pubkey.from_bytes(bytes([9]) * 32), mint, tx.WSOL_MINT, Pubkey.from_bytes(bytes([8]) * 32), bv, qv):
        data += bytes(pk)
    data += (1).to_bytes(8, "little") + bytes(Pubkey.from_bytes(bytes([7]) * 32)) + bytes(2) + V.to_bytes(16, "little", signed=True)
    tok = lambda n: bytes(64) + n.to_bytes(8, "little") + bytes(93)
    return {"pool": str(pool), "mint": str(mint), "pool_data": bytes(data),
            "accounts": {str(bv): acct(tok(b_res), str(tx.TOKEN_PROGRAM)), str(qv): acct(tok(q_vault), str(tx.TOKEN_PROGRAM))},
            "mint_data": bytes(36) + SUPPLY.to_bytes(8, "little") + bytes(38)}


def event(kind: str, pool: str, bps) -> bytes:
    ev = bytearray(8 + 360)
    ev[:8] = td._BUY_DISC if kind == "buy" else td._SELL_DISC
    ev[120:152] = bytes(Pubkey.from_string(pool))
    for off, v in zip(d.FEE_BPS_OFFSETS, bps):
        ev[off : off + 8] = int(v).to_bytes(8, "little")
    return td.ANCHOR_EVENT_IX_TAG + bytes(ev)


class FakeRpc:
    def __init__(self, pools, *, sell_bps=None, sim_err=None, fee_cfg=FEE_CFG, observed=TIER0, drop_event=None, program_id_form=False):
        self.program_id_form = program_id_form
        self.p = {x["pool"]: x for x in pools}
        self.sell_bps, self.sim_err, self.fee_cfg, self.observed, self.drop_event = sell_bps or {}, sim_err or {}, fee_cfg, observed, drop_event
        self.methods: list[str] = []
        self.accts: dict[str, dict] = {}
        for x in pools:
            self.accts.update(x["accounts"])
            self.accts[x["mint"]] = acct(x["mint_data"], str(tx.TOKEN_PROGRAM))
        self.accts[str(tx.GLOBAL_CONFIG)] = acct(bytes(949), OWNER)
        self.accts[str(tx.fee_config())] = acct(fee_cfg, str(tx.FEE_PROGRAM))

    def __call__(self, method, params):
        self.methods.append(method)
        if method == "getAccountInfo":
            key = params[0]
            if key in self.p:
                return {"value": acct(self.p[key]["pool_data"], OWNER)}
            return {"value": self.accts.get(key)}
        if method == "getMultipleAccounts":
            return {"context": {"slot": 77}, "value": [self.accts.get(k) for k in params[0]]}
        if method == "simulateTransaction":
            t = VersionedTransaction.from_bytes(base64.b64decode(params[0]))
            assert params[1]["sigVerify"] is False and params[1]["replaceRecentBlockhash"] is True and params[1]["innerInstructions"] is True
            keys = [str(k) for k in t.message.account_keys]
            pool = next(k for k in keys if k in self.p)
            val: dict = {"err": self.sim_err.get(pool), "unitsConsumed": 123456, "innerInstructions": []}
            if val["err"] is None:
                pidx = keys.index(OWNER)
                evs = [("buy", self.observed), ("sell", self.sell_bps.get(pool, self.observed))]
                evs = [e for e in evs if e[0] != self.drop_event]
                if self.program_id_form:  # the public node's form: base58 programId, parsed token-program noise alongside
                    ixs = [{"parsed": {"type": "getAccountDataSize"}, "program": "spl-token", "programId": str(tx.TOKEN_PROGRAM), "stackHeight": 2}]
                    ixs += [{"programId": OWNER, "accounts": [], "data": td.b58encode(event(k, pool, b)), "stackHeight": 2} for k, b in evs]
                else:
                    ixs = [{"programIdIndex": pidx, "accounts": [0], "data": td.b58encode(event(k, pool, b)), "stackHeight": 2} for k, b in evs]
                val["innerInstructions"] = [{"index": 5, "instructions": ixs}]
            return {"context": {"slot": 4242}, "value": val}
        raise AssertionError(method)


def run(fake, pools, **kw):
    return d.run(fake, [x["pool"] for x in pools], PINS, **kw)


class DryRunTests(unittest.TestCase):
    def pools(self, n=5):
        return [make_pool(i) for i in range(n)]

    def test_pass_with_five_pools(self):
        ps = self.pools()
        fake = FakeRpc(ps)
        code, out = run(fake, ps)
        self.assertEqual(code, d.EXIT_PASS)
        s = out["summary"]
        self.assertEqual((s["verdict"], s["round_trips_ok"], s["n_fee_match"], s["n_fee_mismatch"], s["n_sim_errors"]), ("PASS", 5, 5, 0, 0))
        r = out["pools"][0]
        self.assertEqual(r["observed_bps"], {"buy": list(TIER0), "sell": list(TIER0)})
        self.assertEqual(r["expected_bps"], {"buy": list(TIER0), "sell": list(TIER0)})
        self.assertEqual((r["context_slot"], r["units_consumed"], r["events_found"]), (4242, 123456, {"buy": 1, "sell": 1}))
        self.assertTrue(set(fake.methods) <= d.ALLOWED_METHODS)
        self.assertEqual(d.forbidden_keys(out), [])

    def test_programid_form_of_inner_instructions(self):
        ps = self.pools()
        code, out = run(FakeRpc(ps, program_id_form=True), ps)
        self.assertEqual((code, out["summary"]["n_fee_match"]), (d.EXIT_PASS, 5))

    def test_transaction_is_executor_shaped(self):
        ps = self.pools(1)
        seen = []
        fake = FakeRpc(ps)
        orig = fake.__call__

        def spy(method, params):
            if method == "simulateTransaction":
                seen.append(VersionedTransaction.from_bytes(base64.b64decode(params[0])).message)
            return orig(method, params)

        run(spy, ps)
        discs = [bytes(ix.data)[:8] for ix in seen[0].instructions if seen[0].account_keys[ix.program_id_index] == tx.PUMPSWAP_PROGRAM]
        self.assertEqual(discs, [tx.DISC_BUY_EXACT_QUOTE_IN, tx.DISC_SELL])  # the executor's buy, then its sell
        compute = [ix for ix in seen[0].instructions if str(seen[0].account_keys[ix.program_id_index]) == "ComputeBudget111111111111111111111111111111"]
        self.assertEqual(len(compute), 2)  # one limit + one price (a second pair would be a duplicate-instruction failure)

    def test_sell_fee_mismatch_fails(self):
        ps = self.pools()
        code, out = run(FakeRpc(ps, sell_bps={ps[2]["pool"]: (2, 93, 31)}), ps)
        self.assertEqual(code, d.EXIT_FAIL)
        s = out["summary"]
        self.assertEqual((s["verdict"], s["n_fee_mismatch"], s["n_fee_match"]), ("FAIL", 1, 4))
        self.assertTrue(out["pools"][2]["buy_match"] and not out["pools"][2]["sell_match"])

    def test_simulate_error_fails(self):
        ps = self.pools()
        code, out = run(FakeRpc(ps, sim_err={ps[1]["pool"]: {"InstructionError": [3, {"Custom": 6004}]}}), ps)
        self.assertEqual(code, d.EXIT_FAIL)
        self.assertEqual(out["summary"]["n_sim_errors"], 1)
        self.assertEqual(out["summary"]["round_trips_ok"], 4)

    def test_fewer_than_five_pools_fails(self):
        ps = self.pools(4)
        code, out = run(FakeRpc(ps), ps)
        self.assertEqual(code, d.EXIT_FAIL)
        self.assertEqual((out["summary"]["verdict"], out["summary"]["round_trips_ok"], out["summary"]["n_fee_mismatch"]), ("FAIL", 4, 0))

    def test_missing_event_fails_closed(self):
        ps = self.pools()
        code, out = run(FakeRpc(ps, drop_event="sell"), ps)
        self.assertEqual(code, d.EXIT_FAIL)
        self.assertEqual(out["summary"]["round_trips_ok"], 0)
        self.assertEqual(out["pools"][0]["problem"], "events_not_decoded")

    def test_observed_equal_to_wrong_tier_fails(self):
        ps = self.pools()
        code, out = run(FakeRpc(ps, observed=TIER1), ps)  # events report a tier the pinned FeeConfig does not give this pool
        self.assertEqual((code, out["summary"]["n_fee_match"]), (d.EXIT_FAIL, 0))

    def test_tier_boundary_pool_fails_closed(self):
        # mcap just under 420 SOL before the buy and over it after: the pre- and post-buy tiers differ
        b = 80 * 10**9 * 0 + (Q_VAULT + V) * SUPPLY // (420 * 10**9 - 1) + 1
        ps = [make_pool(0, b_res=b)] + self.pools()[1:]
        code, out = run(FakeRpc(ps), ps)
        self.assertEqual(out["pools"][0]["problem"], "tier_boundary")
        self.assertEqual(code, d.EXIT_FAIL)

    def test_fee_config_differs_from_pin_exits_4(self):
        ps = self.pools()
        other = bytearray(FEE_CFG)
        other[60] ^= 1
        code, out = run(FakeRpc(ps, fee_cfg=bytes(other)), ps)
        self.assertEqual(code, d.EXIT_DIFFERS)
        self.assertEqual(out["summary"]["reason"], "fee_config_differs_from_pin")
        self.assertNotIn("pools", out)  # nothing was simulated: the decision is the owner's

    def test_tier_rule_boundaries(self):
        # mcap = Q * supply / b in lamports; the last tier with threshold <= mcap
        self.assertEqual(d.tier_bps(TIERS, 100, 10**3, 10**3), TIER0)
        self.assertEqual(d.tier_bps(TIERS, 420 * 10**9, 10**15, 10**15), TIER1)  # exactly at the threshold
        self.assertEqual(d.tier_bps(TIERS, 420 * 10**9 - 1, 10**15, 10**15), TIER0)
        self.assertEqual(d.tier_bps(TIERS, 10**13, 10**9, 10**15), TIERS[-1][1])
        with self.assertRaises(ValueError):
            d.tier_bps(TIERS, 0, 1, 1)

    def test_disallowed_method_is_refused(self):
        with self.assertRaises(d.RpcError):
            d.guarded(lambda m, p: {})("getTokenLargestAccounts", [])
        with self.assertRaises(d.RpcError):
            d.guarded(lambda m, p: {})("sendTransaction", [])

    def test_forbidden_key_scan(self):
        self.assertEqual(d.forbidden_keys({"a": [{"price_x": 1}], "ok": {"fee_bps": 1}}), ["/a[0]/price_x"])


class CliTests(unittest.TestCase):
    def test_cli_pass_writes_blind_output_and_exit_codes(self):
        ps = [make_pool(i) for i in range(5)]
        with tempfile.TemporaryDirectory() as td_:
            pins = Path(td_) / "pins.json"
            pins.write_text(json.dumps(PINS))
            out = Path(td_) / "out.json"
            argv = [a for p in ps for a in ("--pool", p["pool"])] + ["--pins", str(pins), "--out", str(out)]
            self.assertEqual(d.main(argv, rpc=FakeRpc(ps)), 0)
            blob = out.read_text()
            self.assertEqual(json.loads(blob)["summary"]["verdict"], "PASS")
            for bad in ("price", "quote", "amount", "pnl", "slippage"):
                self.assertNotIn(bad, blob.lower())
            self.assertEqual(d.main(argv[:2] + ["--pins", str(pins)], rpc=FakeRpc(ps)), d.EXIT_FAIL)
            self.assertEqual(d.main(["--pool", "not-a-key", "--pins", str(pins)], rpc=FakeRpc(ps)), d.EXIT_USAGE)
            self.assertEqual(d.main(["--pins", str(pins)], rpc=FakeRpc(ps)), d.EXIT_USAGE)
            self.assertEqual(d.main(["--pool", ps[0]["pool"], "--pins", str(Path(td_) / "missing.json")], rpc=FakeRpc(ps)), d.EXIT_USAGE)

    def test_url_is_redacted_in_errors(self):
        url = "https://x.example/?api-key=SECRETKEY123"
        calls = []

        def boom(_t):
            calls.append(1)

        rpc = d.HttpRpc("http://127.0.0.1:9/?api-key=SECRETKEY123", sleep=boom, retries=2)
        with self.assertRaises(d.RpcError) as cm:
            rpc("getAccountInfo", ["x"])
        self.assertNotIn("SECRETKEY123", str(cm.exception))
        self.assertEqual(rpc.calls, 3)  # bounded retry
        self.assertNotIn("SECRETKEY123", d.redact_rpc_url(f"failed {url}"))


if __name__ == "__main__":
    unittest.main()
