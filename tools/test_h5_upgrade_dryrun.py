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


def event(kind: str, pool: str, bps, *, bad_amount: bool = False) -> bytes:
    ev = bytearray(8 + 360)
    ev[:8] = td._BUY_DISC if kind == "buy" else td._SELL_DISC
    ev[120:152] = bytes(Pubkey.from_string(pool))
    base = 19_940_177
    ev[d.FEE_BASE_OFFSET[kind] : d.FEE_BASE_OFFSET[kind] + 8] = base.to_bytes(8, "little")
    for i, (off, v) in enumerate(zip(d.FEE_BPS_OFFSETS, bps)):
        ev[off : off + 8] = int(v).to_bytes(8, "little")
        amt = -((-base * int(v)) // 10_000) + (1 if bad_amount and i == 0 else 0)
        ev[d.FEE_AMOUNT_OFFSETS[i] : d.FEE_AMOUNT_OFFSETS[i] + 8] = amt.to_bytes(8, "little")
    return td.ANCHOR_EVENT_IX_TAG + bytes(ev)


class FakeRpc:
    def __init__(self, pools, *, sell_bps=None, sim_err=None, fee_cfg=FEE_CFG, observed=TIER0, drop_event=None, program_id_form=False, observed_by=None, bad_amount=False):
        self.observed_by, self.bad_amount = observed_by or {}, bad_amount
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
                obs = self.observed_by.get(pool, self.observed)
                evs = [("buy", obs), ("sell", self.sell_bps.get(pool, obs))]
                evs = [e for e in evs if e[0] != self.drop_event]
                if self.program_id_form:  # the public node's form: base58 programId, parsed token-program noise alongside
                    ixs = [{"parsed": {"type": "getAccountDataSize"}, "program": "spl-token", "programId": str(tx.TOKEN_PROGRAM), "stackHeight": 2}]
                    ixs += [{"programId": OWNER, "accounts": [], "data": td.b58encode(event(k, pool, b, bad_amount=self.bad_amount)), "stackHeight": 2} for k, b in evs]
                else:
                    ixs = [{"programIdIndex": pidx, "accounts": [0], "data": td.b58encode(event(k, pool, b, bad_amount=self.bad_amount)), "stackHeight": 2} for k, b in evs]
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

    def test_duplicate_pools_do_not_count(self):
        p = make_pool(0)
        with self.assertRaises(SystemExit):
            d.run(FakeRpc([p]), [p["pool"]] * 5, PINS)
        recs = [{"pool": p["pool"], "round_trip_ok": True, "buy_match": True, "sell_match": True, "sim_err": None, "problem": None,
                 "expected_tier_index": 0, "fee_amount_consistent": {"buy": [True] * 3, "sell": [True] * 3}}] * 5
        s = d.summarize(recs, 5)  # even if records repeat, distinct pools are counted
        self.assertEqual((s["verdict"], s["round_trips_ok"]), ("FAIL", 1))

    def test_min_round_trips_below_dec_minimum_is_refused(self):
        ps = self.pools(1)
        with self.assertRaises(SystemExit):
            d.run(FakeRpc(ps), [ps[0]["pool"]], PINS, min_round_trips=1)
        s = d.summarize([{"pool": "a", "round_trip_ok": True, "buy_match": True, "sell_match": True, "sim_err": None, "problem": None,
                          "expected_tier_index": 0, "fee_amount_consistent": {"buy": [True] * 3, "sell": [True] * 3}}], 1)
        self.assertEqual(s["verdict"], "FAIL")  # the floor applies inside summarize too

    def test_one_pool_run_is_fail_with_records(self):
        ps = self.pools(1)
        code, out = run(FakeRpc(ps), ps)
        self.assertEqual((code, out["summary"]["verdict"], len(out["pools"])), (d.EXIT_FAIL, "FAIL", 1))
        self.assertTrue(out["pools"][0]["round_trip_ok"])

    def test_tier1_pool_passes_with_tier1_events_and_fails_with_tier0(self):
        b1 = 2 * 10**14  # mcap ~ 488 SOL: tier 1
        ps = [make_pool(i, b_res=b1) for i in range(5)]
        code, out = run(FakeRpc(ps, observed=TIER1), ps)
        self.assertEqual(code, d.EXIT_PASS)
        self.assertEqual(out["summary"]["tiers_covered"], [1])
        self.assertEqual({r["expected_tier_index"] for r in out["pools"]}, {1})
        code, out = run(FakeRpc(ps, observed=TIER0), ps)  # the formula, not the table, drives the expectation
        self.assertEqual((code, out["summary"]["n_fee_match"]), (d.EXIT_FAIL, 0))
        mixed = self.pools(3) + [make_pool(7, b_res=b1), make_pool(8, b_res=b1)]
        code, out = run(FakeRpc(mixed, observed_by={mixed[3]["pool"]: TIER1, mixed[4]["pool"]: TIER1}), mixed)
        self.assertEqual((code, out["summary"]["tiers_covered"]), (d.EXIT_PASS, [0, 1]))

    def test_expectation_inputs_are_pinned_by_tier_flips(self):
        b = 22 * 10**13
        q = Q_VAULT + V
        self.assertEqual(d.tier_index(TIERS, q, b, SUPPLY), 1)
        self.assertEqual(d.tier_index(TIERS, Q_VAULT, b, SUPPLY), 0)  # dropping V flips the tier
        self.assertEqual(d.tier_index(TIERS, q, b, 10**9), 0)  # the 10^9-token supply flips it
        self.assertEqual(d.tier_index(TIERS, q, b, 10**16), 2)  # a larger real supply flips it the other way
        # end to end: V is read from the pool account and supply from the mint, so tier-1 events pass only if both are used
        ps = [make_pool(i, b_res=b) for i in range(5)]
        self.assertEqual(run(FakeRpc(ps, observed=TIER1), ps)[0], d.EXIT_PASS)
        self.assertEqual(run(FakeRpc(ps, observed=TIER0), ps)[0], d.EXIT_FAIL)

    def test_rpc_without_result_fails_closed(self):
        ps = self.pools()
        with tempfile.TemporaryDirectory() as td_:
            pins = Path(td_) / "pins.json"
            pins.write_text(json.dumps(PINS))
            argv = [a for p in ps for a in ("--pool", p["pool"])] + ["--pins", str(pins)]
            self.assertEqual(d.main(argv, rpc=lambda m, p: None), d.EXIT_FAIL)

            def no_key(method, params):
                raise KeyError("result")

            self.assertEqual(d.main(argv, rpc=no_key), d.EXIT_FAIL)
        # mid-run: pool reads fail, the FeeConfig read works
        fake = FakeRpc(ps)

        def flaky(method, params):
            if method == "getMultipleAccounts":
                return None
            return fake(method, params)

        code, out = run(flaky, ps)
        self.assertEqual(code, d.EXIT_FAIL)
        self.assertTrue(all(r["problem"] for r in out["pools"]))

    def test_fee_amount_consistency_is_report_only(self):
        ps = self.pools()
        code, out = run(FakeRpc(ps), ps)
        s = out["summary"]
        self.assertEqual((code, s["fee_amount_legs_checked"], s["fee_amount_legs_consistent"]), (d.EXIT_PASS, 30, 30))
        code, out = run(FakeRpc(ps, bad_amount=True), ps)  # one leg per event is off by one lamport: reported, verdict unchanged
        s = out["summary"]
        self.assertEqual((code, s["verdict"], s["fee_amount_legs_checked"], s["fee_amount_legs_consistent"]), (d.EXIT_PASS, "PASS", 30, 20))
        self.assertEqual(out["pools"][0]["fee_amount_consistent"]["buy"], [False, True, True])
        self.assertEqual(d.forbidden_keys(out), [])
        self.assertTrue(all(isinstance(x, bool) for r in out["pools"] for sd in r["fee_amount_consistent"].values() for x in sd))

    def test_summary_discloses_inputs_and_executor_defaults(self):
        ps = self.pools()
        _, out = run(FakeRpc(ps), ps)
        self.assertEqual((out["summary"]["buy_slippage_bps"], out["summary"]["sell_close_omitted"]), (300, True))
        self.assertEqual(d.executor_cu_limits(), (tx.DEFAULT_BUY_CU_LIMIT, tx.DEFAULT_SELL_CU_LIMIT))
        import dataclasses
        from tools.h5_executor import H5Limits
        self.assertTrue({f.name: f.default for f in dataclasses.fields(H5Limits)}["track_volume"])

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
            self.assertEqual(d.forbidden_keys(json.loads(blob)), [])
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
