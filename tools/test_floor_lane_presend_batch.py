"""Offline tests for the batched pre-send reads of the live port (arena_live_bridge.make_live_port, opt-in `presend_batch`) and the per-stage stamps on buy_sent.

The same Bridge, the same decision row and the same fake chain run through the old serial reads and the new batched reads; the decision outputs (refusal label, order
status, the signed bytes that would be sent) must be identical. A fake clock drives the real LimitedRpc at 4 rps, so the measured decision-to-send here is the limiter's
arithmetic over scripted calls with zero network time: a test timing, not a landing."""

from __future__ import annotations

import json
import unittest
import unittest.mock
from pathlib import Path

from tools import arena_live_bridge as alb
from tools import mint_account_fetch as mf
from tools import probe_executor as pe
from tools.test_arena_live_bridge import Env, TmpCase
from tools.test_arena_live_bridge_mint_screen import ALLOW, mint_value

RPS = 4.0


def _stack():
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey

    from tools import pumpswap_tx as tx
    from tools.test_probe_executor import MINT, no_v_pool
    from tools.test_probe_live import LiveRpc

    return Keypair, Pubkey, tx, MINT, no_v_pool, LiveRpc


def make_rpc(clock, mint, wallet, mints, balance=500_000_000, pool_b64=None, fail=()):
    """A fake chain: the probe tests' LiveRpc plus mint accounts, a wallet balance, and a getMultipleAccounts that serves [pool, mint?, wallet] like the chain.
    `fail`: method names that raise a timeout; every call is recorded as (method, clock ms)."""
    _, _, tx, _, _, LiveRpc = _stack()

    class Rpc(LiveRpc):
        def __init__(self):
            super().__init__(clock, **({"pool_b64": pool_b64} if pool_b64 else {}))
            self.balance = balance
            self.log: list[tuple[str, int]] = []

        def __call__(self, method, params):
            self.log.append((method, clock.t))
            if method in fail:
                raise pe.RpcError("timeout")
            if method == "getAccountInfo" and params[0] in mints:
                self.calls.append(method)
                return {"context": {"slot": 77}, "value": mints[params[0]]}
            if method == "getMultipleAccounts" and params[0] and params[0][0] == str(tx.canonical_pool(__import__("solders.pubkey", fromlist=["Pubkey"]).Pubkey.from_string(mint))):
                self.calls.append(method)
                keys = params[0]
                pool = super().__call__("getAccountInfo", [keys[0], {}])["value"]
                out = [pool]
                for k in keys[1:-1]:
                    out.append(mints.get(k))
                out.append({"lamports": self.balance, "owner": "11111111111111111111111111111111", "data": ["", "base64"], "executable": False} if self.balance is not None else None)
                return {"context": {"slot": 77}, "value": out}
            return super().__call__(method, params)

        def reads(self):
            return [m for m, _ in self.log if m != "sendTransaction"]

    return Rpc()


class Run:
    """One entry through the Bridge over the real live port, old (batch False) or new (batch True), on a fake clock that the 4 rps limiter sleeps on."""

    def __init__(self, tmp, batch, *, mint_val=None, balance=500_000_000, pool_b64=None, fail=(), hint=True):
        Keypair, Pubkey, tx, MINT, _, _ = _stack()
        self.e = Env(str(tmp), live=True)
        e = self.e
        self.mint = MINT
        pool = str(tx.canonical_pool(Pubkey.from_string(MINT)))
        kp = Keypair.from_seed(bytes(range(32)))  # one key for both runs: the signed bytes are comparable
        mints = {MINT: mint_val if mint_val is not None else mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW)}
        self.rpc = make_rpc(e.clock, MINT, str(kp.pubkey()), mints, balance, pool_b64, fail)

        def sleep(s):
            e.clock.t += int(round(s * 1000))

        self.limited = pe.LimitedRpc(self.rpc, rps=RPS, max_rps=5.0, clock=lambda: e.clock.t / 1000.0, sleep=sleep)
        raw = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")} | {"mode": "live"}
        cfg = alb.Config.from_dict(raw)
        self.port = alb.make_live_port(cfg, self.limited, kp, now_ms=e.clock)
        if batch:
            self.port.enable_presend_batch()
        e.port = self.port
        e.live_ok.write_text("ok")  # root_checks is off in the test Bridge: the file's presence is LIVE_OK
        e.start()
        e.entry("o1", pool=pool, **({"mint": MINT} if hint else {}))
        self.bridge = e.bridge()
        self.bridge.tick()

    def outcome(self):
        """The decision outputs: refusal labels, order status/refusal, whether and WHAT was sent (signed bytes), the stake."""
        e = self.e
        o = self.bridge.state.orders.get("o1")
        return {"refused": [r["why"] for r in e.log_rows("entry_refused")], "status": o and o["status"], "order_refused": o and o.get("refused"),
                "stake": o and o["stake_lamports"], "sent": [b for _, b in self.rpc.sent], "live": o and o["live"]}


class EquivalenceTests(TmpCase):
    def setUp(self):
        super().setUp()
        try:
            _stack()
        except ModuleNotFoundError as exc:
            self.skipTest(str(exc))

    def both(self, name, **kw):
        olddir, newdir = Path(self.tmp) / f"{name}-old", Path(self.tmp) / f"{name}-new"
        olddir.mkdir()
        newdir.mkdir()
        return Run(olddir, False, **kw), Run(newdir, True, **kw)

    def same(self, name, expect_sent, **kw):
        old, new = self.both(name, **kw)
        self.assertEqual(old.outcome(), new.outcome(), name)
        self.assertEqual(bool(new.outcome()["sent"]), expect_sent, name)
        return old, new

    def test_a_clear_entry_sends_the_same_bytes(self):
        old, new = self.same("clear", True)
        self.assertEqual(len(old.outcome()["sent"]), 1)

    def test_each_failing_check_blocks_the_send_with_the_same_label(self):
        cases = {
            "blocked_mint": dict(mint_val=mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW + (14,))),
            "freeze": dict(mint_val=mint_value(freeze=True)),
            "wallet_floor": dict(balance=1),
            "v_out_of_range": dict(pool_b64=_stack()[4]()),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                old, new = self.both(name, **kw)
                self.assertEqual(old.outcome(), new.outcome())
                self.assertEqual(new.outcome()["sent"], [])
                self.assertTrue(new.outcome()["refused"], name)

    def test_the_old_labels_are_the_expected_ones(self):
        _, new = self.both("lbl-blocked", mint_val=mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW + (14,)))
        self.assertEqual(new.outcome()["refused"], ["close_blocked_mint:extension_outside_allowlist"])
        _, new = self.both("lbl-floor", balance=1)
        self.assertEqual(new.outcome()["refused"], ["wallet_floor"])
        _, new = self.both("lbl-v", pool_b64=_stack()[4]())
        self.assertEqual(new.outcome()["refused"], ["universe_v_out_of_range"])

    def test_a_missing_mint_hint_falls_back_to_the_serial_mint_read_with_the_same_outcome(self):
        old, new = self.both("nohint", hint=False)
        self.assertEqual(old.outcome(), new.outcome())
        self.assertEqual(new.rpc.reads().count("getAccountInfo"), 2)  # the mint screen read for itself + the buy's own fresh snapshot read; the old path has 4

    def test_a_timeout_on_the_batched_read_fails_closed(self):
        e_old, e_new = self.both("timeout", fail=("getMultipleAccounts", "getAccountInfo"))
        for r in (e_old, e_new):
            self.assertEqual(r.outcome()["sent"], [])
            self.assertEqual(r.outcome()["refused"], ["no_mint"])
        self.assertEqual(e_old.outcome(), e_new.outcome())

    def test_a_short_batched_answer_fails_closed(self):
        d = Path(self.tmp) / "short"
        d.mkdir()
        port = Run(d, False).port  # reuse the built port; then answer the batch with one value for three keys
        port.enable_presend_batch()
        port.ex.rpc = lambda m, p: {"context": {"slot": 1}, "value": [None]}
        port.prefetch_presend("P", {"mint": _stack()[3]})
        self.assertIsNone(port.mint_for_pool("P", {}))
        self.assertIsNone(port.presend_balance())
        self.assertTrue(port.universe_problem("P", _stack()[3]).startswith("pool_unreadable:"))
        self.assertTrue(port.mint_screen_problem(_stack()[3]).startswith(alb.MINT_SCREEN_LABEL + ":"))


class RateAndTimingTests(TmpCase):
    def setUp(self):
        super().setUp()
        try:
            _stack()
        except ModuleNotFoundError as exc:
            self.skipTest(str(exc))

    def runs(self):
        a, b = Path(self.tmp) / "o", Path(self.tmp) / "n"
        a.mkdir()
        b.mkdir()
        return Run(a, False), Run(b, True)

    def test_every_read_goes_through_the_one_limiter_and_the_rate_holds(self):
        old, new = self.runs()
        for r in (old, new):
            self.assertEqual(r.limited.calls, len(r.rpc.log))  # no call bypassed the limiter
            ts = [t for _, t in r.rpc.log]
            iv = 1000.0 / min(RPS, 5.0)
            burst = (r.limited.burst - 1) * iv
            for i, t in enumerate(ts):  # the schedule never runs more than `burst` ahead of one call per interval
                self.assertGreaterEqual(t - ts[0], i * iv - burst - 1, (i, ts))
        self.assertLess(len(new.rpc.log), len(old.rpc.log))
        self.assertEqual([m for m in new.rpc.reads()][:2], ["getMultipleAccounts", "getMultipleAccounts"])

    def test_the_batched_reads_are_one_call_instead_of_four(self):
        old, new = self.runs()
        pre_old = [m for m in old.rpc.reads() if m != "getLatestBlockhash"]
        self.assertEqual(old.rpc.reads()[:5], ["getAccountInfo", "getAccountInfo", "getMultipleAccounts", "getBalance", "getAccountInfo"])  # the five serial pre-send reads
        self.assertEqual(new.rpc.reads()[:2], ["getMultipleAccounts", "getMultipleAccounts"])  # batch (pool+mint+wallet), then the vaults of the snapshot
        self.assertEqual(len(pre_old) - len([m for m in new.rpc.reads() if m != "getLatestBlockhash"]), 3)

    def test_decision_to_send_drops_at_4_rps(self):
        old, new = self.runs()
        t_old = old.e.log_rows("buy_sent")[0]["decision_to_send_ms"]
        t_new = new.e.log_rows("buy_sent")[0]["decision_to_send_ms"]
        print(f"\nfake-clock decision_to_send_ms at {RPS} rps (zero network time): old serial {t_old}, new batched {t_new}")
        self.assertLess(t_new, t_old)
        self.assertEqual(old.outcome()["sent"] != [], True)

    def test_stage_stamps_on_buy_sent(self):
        _, new = self.runs()
        row = new.e.log_rows("buy_sent")[0]
        st = row["stage_ms"]
        order = ["entry_ms", "checked", "prefetch", "reads", "writeahead", "send_call", "buy_return"]
        self.assertEqual(sorted(st), sorted(order))
        self.assertTrue(all(isinstance(v, int) for v in st.values()))
        self.assertEqual([st[k] for k in order], sorted(st.values()))  # in stage order
        ex = row["exec_stage_ms"]
        for k in ("state_ms", "built_ms", "first_send_ms", "state_slot"):
            self.assertIn(k, ex)
        self.assertLessEqual(ex["state_ms"], ex["built_ms"])
        self.assertLessEqual(ex["built_ms"], ex["first_send_ms"])
        self.assertEqual(row["decision_to_send_ms"], row["send_ms"] - row["decision_ms"])  # existing fields unchanged


class OffByDefaultTests(TmpCase):
    def test_default_port_makes_no_batched_call(self):
        try:
            _stack()
        except ModuleNotFoundError as exc:
            self.skipTest(str(exc))
        d = Path(self.tmp) / "d"
        d.mkdir()
        r = Run(d, False)
        self.assertEqual(r.rpc.reads()[:5], ["getAccountInfo", "getAccountInfo", "getMultipleAccounts", "getBalance", "getAccountInfo"])
        self.assertFalse(r.port.batch_presend)


class ConfigKeyTests(unittest.TestCase):
    def test_parse_presend_batch(self):
        from tools import floor_lane_executor as X

        self.assertIs(X.parse_presend_batch({}), False)
        self.assertIs(X.parse_presend_batch({"presend_batch": True}), True)
        self.assertIs(X.parse_presend_batch({"presend_batch": False}), False)
        for bad in (1, 0, "true", None, [True]):
            with self.assertRaises(ValueError):
                X.parse_presend_batch({"presend_batch": bad})

    def test_the_key_reaches_the_live_port_only_when_true(self):
        import tempfile

        from tools import floor_lane_executor as X
        from tools import probe_live as pl
        from tools import pumpswap_simulate as sim
        from tools.test_floor_lane_executor import FakeInner, lane_raw

        class Inner(FakeInner):
            enabled = False

            def enable_presend_batch(self):
                self.enabled = True

        made: list = []
        mk = unittest.mock.patch
        with tempfile.TemporaryDirectory() as d, mk.object(pl, "harden_process", lambda: None), mk.object(pl, "load_probe_key", lambda *a, **k: object()), \
                mk.object(sim, "load_rpc_url", lambda *a, **k: "http://rpc.invalid"), mk.object(pe, "ProbeRpc", lambda url: url), \
                mk.object(pe, "LimitedRpc", lambda rpc, rps, max_rps: "rpc"), mk.object(alb, "make_live_port", lambda cfg, rpc, key: made.append(Inner()) or made[-1]), \
                mk.dict("os.environ", {"HELIUS_API_KEY": "x" * 8}):
            cfg, _lane, _ok = X.make_config(lane_raw(Path(d)))
            X.build_live_port(cfg)
            X.build_live_port(cfg, presend_batch=True)
        self.assertEqual([i.enabled for i in made], [False, True])


if __name__ == "__main__":
    unittest.main()
