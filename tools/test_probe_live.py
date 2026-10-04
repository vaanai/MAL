"""Offline tests for tools/probe_live.py (DEC-019 PR-B). Fake RPC, throwaway Keypair generated per test, no network."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from tools import probe_executor as pe
from tools import probe_live as pl
from tools import pumpswap_tx as tx
from tools.test_probe_executor import MINT, T0, Clock, FakeRpc, fills, sig as mk_sig

LVBH = 1_000
BAL = 500_000_000
RENT = 2_039_280


class LiveRpc(FakeRpc):
    """FakeRpc plus the live methods. `statuses` and `txs` are keyed by signature (scripted by tests)."""

    def __init__(self, clock: Clock, **kw):
        super().__init__(**kw)
        self.clock = clock
        self.sent: list[tuple[int, str]] = []
        self.statuses: dict[str, dict] = {}
        self.txs: dict[str, dict] = {}
        self.height = 900
        self.balance = BAL
        self.token_balance = 0
        self.fetches = 0
        self.send_hook = None
        self.send_fails = False

    def __call__(self, method, params):
        if method == "getBalance":
            self.calls.append(method)
            return {"context": {"slot": 1}, "value": self.balance}
        if method == "getLatestBlockhash":
            self.calls.append(method)
            self.fetches += 1  # a new hash per fetch, as on chain
            return {"context": {"slot": 1}, "value": {"blockhash": str(Hash(self.fetches.to_bytes(32, "little"))), "lastValidBlockHeight": LVBH}}
        if method == "getBlockHeight":
            self.calls.append(method)
            return self.height
        if method == "sendTransaction":
            self.calls.append(method)
            if self.send_hook:
                self.send_hook(params)
            if self.send_fails:
                raise pe.RpcError("timeout")
            raw = base64.b64decode(params[0])
            assert params[1] == {"encoding": "base64", "skipPreflight": True, "maxRetries": 0}
            self.sent.append((self.clock(), params[0]))
            return str(VersionedTransaction.from_bytes(raw).signatures[0])
        if method == "getSignatureStatuses":
            self.calls.append(method)
            return {"context": {"slot": 1}, "value": [self.statuses.get(s) for s in params[0]]}
        if method == "getTransaction":
            self.calls.append(method)
            return self.txs.get(params[0])
        if method == "getTokenAccountBalance":
            self.calls.append(method)
            return {"value": {"amount": str(self.token_balance)}}
        return super().__call__(method, params)


def make_live(tmp: Path, **cfg):
    clock = Clock()
    rpc = LiveRpc(clock)
    kp = Keypair()
    conf = {"signals_dir": str(tmp / "sig"), "state_dir": str(tmp), "fill_log": str(tmp / "fills.jsonl"),
            "stop_file": str(tmp / "STOP"), "halt_file": str(tmp / "HALT"), "mode": "live", "poll_s": 5.0, **cfg}
    (tmp / "sig").mkdir(exist_ok=True)
    return pl.LiveExecutor(rpc, conf, kp, now_ms=clock), rpc, clock, kp, conf


def meta_result(ex, mint, *, delta, fee, tok_delta=0, err=None, ata_pre=0, ata_post=0, slot=2_000, logs=None):
    src = ex.state.pending[mint] if mint in ex.state.pending and "base_ata" in ex.state.pending[mint] else ex.state.open[mint]
    ata, bm = src["base_ata"], src["base_mint"]

    def row(amount):
        return [{"accountIndex": 1, "mint": bm, "owner": str(ex.user), "uiTokenAmount": {"amount": str(amount)}}]

    pre_tok = row(-tok_delta) if tok_delta < 0 else []
    post_tok = row(tok_delta) if tok_delta > 0 else []
    return {"slot": slot, "transaction": {"message": {"accountKeys": [str(ex.user), ata]}},
            "meta": {"err": err, "fee": fee, "preBalances": [BAL, ata_pre], "postBalances": [BAL + delta, ata_post],
                     "preTokenBalances": pre_tok, "postTokenBalances": post_tok, "logMessages": logs or []}}


def signal_buy(ex, clock):
    ex.handle_signal(mk_sig(ex, t=clock()))
    return ex.state.pending.get(MINT)


def land_buy(ex, rpc, tok=None, slot=2_000):
    p = ex.state.pending[MINT]
    tok = tok or p["q_tokens"]
    spend, fee = p["spend"], 5_000 + 495_000
    rpc.statuses[p["signature"]] = {"slot": slot, "confirmationStatus": "confirmed", "err": None}
    rpc.txs[p["signature"]] = meta_result(ex, MINT, delta=-(spend + fee + RENT), fee=fee, tok_delta=tok, ata_post=RENT, slot=slot)
    rpc.token_balance = tok
    ex.advance_pending()


def open_position(ex, rpc, clock, tok=None):
    signal_buy(ex, clock)
    land_buy(ex, rpc, tok)
    assert MINT in ex.state.open
    return ex.state.open[MINT]


def land_sell(ex, rpc, *, proceeds, fee=500_000 + 5_000, err=None, logs=None):
    p = ex.state.pending[MINT]
    delta = proceeds - fee + RENT
    rpc.statuses[p["signature"]] = {"slot": 3_000, "confirmationStatus": "confirmed", "err": err}
    rpc.txs[p["signature"]] = meta_result(ex, MINT, delta=(-fee if err else delta), fee=fee, tok_delta=-p["tokens"] if not err else 0,
                                          err=err, ata_pre=RENT, ata_post=0 if not err else RENT, slot=3_000, logs=logs)
    ex.advance_pending()


class GatingTests(unittest.TestCase):
    def test_two_switches(self):
        self.assertEqual(pe.resolve_mode("live", True), ("live", None))
        for cfg_mode, flag in (("live", False), ("dryrun", True)):
            mode, warn = pe.resolve_mode(cfg_mode, flag)
            self.assertEqual(mode, "dryrun")
            self.assertIn("DRY RUN", warn)
        self.assertEqual(pe.resolve_mode("dryrun", False), ("dryrun", None))
        with self.assertRaises(SystemExit):
            pe.resolve_mode("paper", True)

    def test_main_goes_live_only_with_both(self):
        with tempfile.TemporaryDirectory() as d:
            conf = {"signals_dir": d, "state_dir": d, "fill_log": f"{d}/f.jsonl", "mode": "live"}
            cp = Path(d) / "c.json"
            cp.write_text(json.dumps(conf))
            with mock.patch.object(pl, "run_live", return_value=0) as rl, mock.patch.object(pe.sim, "load_rpc_url", return_value="http://x"), \
                    mock.patch.object(pe.Executor, "step", return_value=0), contextlib.redirect_stdout(io.StringIO()) as out:
                pe.main(["--config", str(cp), "--once"])  # config live, no flag
                rl.assert_not_called()
                self.assertIn("mode=dryrun", out.getvalue())
                self.assertIn("DRY RUN", out.getvalue())
                conf["mode"] = "dryrun"
                cp.write_text(json.dumps(conf))
                pe.main(["--config", str(cp), "--once", "--live"])  # flag, config dryrun
                rl.assert_not_called()
                conf["mode"] = "live"
                cp.write_text(json.dumps(conf))
                pe.main(["--config", str(cp), "--once", "--live"])
                rl.assert_called_once()

    def test_shipped_unit_config_is_dryrun(self):
        root = Path(__file__).parent.parent
        cfg = json.loads((root / "scripts/mal-fast/probe-executor.json").read_text())
        self.assertEqual(cfg["mode"], "dryrun")
        self.assertNotIn("--live", (root / "scripts/mal-fast/mal-probe-executor.service").read_text())
        drop = (root / "scripts/mal-fast/mal-probe-executor-live.conf").read_text()
        self.assertIn("--live", drop)
        self.assertIn("probe-executor-live.json", drop)
        self.assertEqual(json.loads((root / "scripts/mal-fast/probe-executor-live.json").read_text())["mode"], "live")


class KeyTests(unittest.TestCase):
    def write(self, d, kp, mode):
        p = Path(d) / "k.json"
        p.write_text(json.dumps(list(bytes(kp))))
        os.chmod(p, mode)
        return str(p)

    def test_loads_good_modes(self):
        kp = Keypair()
        with tempfile.TemporaryDirectory() as d:
            for mode in (0o400, 0o600):
                self.assertEqual(pl.load_probe_key(self.write(d, kp, mode), verify_chain=False).pubkey(), kp.pubkey())
                os.chmod(Path(d) / "k.json", 0o600)

    def test_refuses_bad_mode_owner_symlink_and_malformed(self):
        kp = Keypair()
        with tempfile.TemporaryDirectory() as d:
            for mode in (0o644, 0o640, 0o604, 0o700, 0o666):
                path = self.write(d, kp, mode)
                with self.assertRaises(SystemExit) as cm:
                    pl.load_probe_key(path, verify_chain=False)
                self.assertNotIn(str(kp.pubkey()), str(cm.exception))
                os.chmod(path, 0o600)
            path = self.write(d, kp, 0o600)
            with mock.patch("os.geteuid", return_value=os.geteuid() + 1), self.assertRaises(SystemExit):
                pl.load_probe_key(path, verify_chain=False)
            link = Path(d) / "link.json"
            link.symlink_to(path)
            with self.assertRaises(SystemExit):
                pl.load_probe_key(str(link), verify_chain=False)
            Path(path).write_text("[1,2,3]")
            with self.assertRaises(SystemExit) as cm:
                pl.load_probe_key(path, verify_chain=False)
            self.assertNotIn("1,2,3", str(cm.exception))
            with self.assertRaises(SystemExit):
                pl.load_probe_key(str(Path(d) / "missing.json"), verify_chain=False)

    def test_parent_chain(self):
        import types

        def fake(table):
            return lambda d: types.SimpleNamespace(st_uid=table[d][0], st_mode=table[d][1])

        good = {"/": (0, 0o755), "/var": (0, 0o755), "/var/lib": (0, 0o755), "/var/lib/mal-live": (os.geteuid(), 0o700)}
        pl.check_parent_chain("/var/lib/mal-live/probe-wallet.json", fake(good))  # ok
        for path, entry in (("/var/lib", (1000, 0o755)), ("/var", (0, 0o775)), ("/", (0, 0o757)),
                            ("/var/lib/mal-live", (os.geteuid() + 5, 0o700)), ("/var/lib/mal-live", (os.geteuid(), 0o770))):
            bad = {**good, path: entry}
            with self.assertRaises(SystemExit, msg=path), mock.patch("os.path.realpath", side_effect=lambda p: p):
                pl.check_parent_chain("/var/lib/mal-live/probe-wallet.json", fake(bad))

    def test_load_checks_chain_by_default(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit):
            kp = Keypair()
            pl.load_probe_key(self.write(d, kp, 0o400))  # /tmp ancestors are not root-owned/unwritable-safe

    def test_default_key_path(self):
        self.assertEqual(pl.KEY_PATH, "/var/lib/mal-live/probe-wallet.json")

    def test_harden_process(self):
        import ctypes
        import resource

        try:
            with mock.patch.object(resource, "setrlimit") as srl:  # lowering the hard limit is irreversible
                pl.harden_process()
            srl.assert_called_once_with(resource.RLIMIT_CORE, (0, 0))
            self.assertEqual(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0), 0)  # PR_GET_DUMPABLE
        finally:
            ctypes.CDLL(None).prctl(4, 1, 0, 0, 0)

    def test_run_live_hardens_before_loading_key(self):
        order = []

        def key(_p):
            order.append("key")
            raise SystemExit("stop")

        with mock.patch.object(pl, "harden_process", lambda: order.append("harden")), mock.patch.object(pl, "load_probe_key", side_effect=key):
            with self.assertRaises(SystemExit):
                pl.run_live({"mode": "live"}, mock.Mock(env_file="x"), 5.0)
        self.assertEqual(order, ["harden", "key"])


class BuyFlowTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)
        self.ex, self.rpc, self.clock, self.kp, self.conf = make_live(self.tmp)

    def tearDown(self):
        self._d.cleanup()

    def test_buy_is_signed_by_probe_key_and_write_ahead(self):
        seen = {}

        def hook(params):  # runs inside sendTransaction: state must already be durable
            st = json.loads((self.tmp / "state-live.json").read_text())
            seen["attempts"] = st["attempts"]
            seen["sig"] = st["pending"][MINT]["signature"]

        self.rpc.send_hook = hook
        p = signal_buy(self.ex, self.clock)
        self.assertEqual(seen, {"attempts": 1, "sig": p["signature"]})
        t = VersionedTransaction.from_bytes(base64.b64decode(self.rpc.sent[0][1]))
        self.assertEqual(str(t.signatures[0]), p["signature"])
        self.assertEqual(t.message.account_keys[0], self.kp.pubkey())
        self.assertEqual(p["lvbh"], LVBH)

    def test_restart_resumes_signature_and_does_not_rebuy(self):
        self.rpc.send_fails = True  # crash / failed send: only the write-ahead record exists
        p = signal_buy(self.ex, self.clock)
        ex2 = pl.LiveExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock)
        self.assertEqual(ex2.state.pending[MINT]["signature"], p["signature"])
        self.assertEqual(ex2.state.attempts, 1)
        self.rpc.send_fails = False
        self.clock.t += 3_000
        ex2.advance_pending()  # no status yet -> rebroadcast the SAME tx
        self.assertEqual(len(self.rpc.sent), 1)
        self.assertEqual(self.rpc.sent[0][1], p["tx_b64"])
        land_buy(ex2, self.rpc)
        self.assertIn(MINT, ex2.state.open)
        self.assertEqual(ex2.state.attempts, 1)
        ex2.handle_signal(mk_sig(ex2, t=self.clock()))  # the same decision again: no second buy
        self.assertEqual(ex2.state.attempts, 1)
        self.assertEqual(len(self.rpc.sent), 1)
        self.assertEqual(fills(self.conf)[-1]["reason"], "already_open")

    def test_rebroadcast_same_tx_until_confirmed(self):
        p = signal_buy(self.ex, self.clock)
        for _ in range(5):
            self.clock.t += 2_000
            self.ex.advance_pending()
        self.assertEqual(len(self.rpc.sent), 6)
        self.assertEqual({b for _, b in self.rpc.sent}, {p["tx_b64"]})
        self.assertEqual({b - a for (a, _), (b, _) in zip(self.rpc.sent, self.rpc.sent[1:])}, {2_000})
        land_buy(self.ex, self.rpc)
        n = len(self.rpc.sent)
        self.clock.t += 10_000
        self.ex.advance_pending()
        self.assertEqual(len(self.rpc.sent), n)  # nothing pending: no more sends
        self.assertEqual(self.ex.state.attempts, 1)

    def test_expiry_stops_rebroadcast_and_never_rebuys(self):
        signal_buy(self.ex, self.clock)
        self.rpc.height = LVBH + 1
        self.clock.t += 2_000
        self.ex.advance_pending()
        self.assertIn(MINT, self.ex.state.pending)  # first sighting: look once more
        n = len(self.rpc.sent)
        self.clock.t += pl.EXPIRY_RECHECK_MS
        self.ex.advance_pending()
        self.assertNotIn(MINT, self.ex.state.pending)
        self.assertEqual(len(self.rpc.sent), n)
        row = [r for r in fills(self.conf) if r["kind"] == "buy"][-1]
        self.assertEqual((row["landed"], row["fail_class"]), (False, "expired"))
        self.assertEqual(self.ex.state.attempts, 1)
        self.assertEqual(self.ex.state.realized_lamports, 0)

    def test_expired_but_landed_late_is_seen(self):
        signal_buy(self.ex, self.clock)
        self.rpc.height = LVBH + 1
        self.ex.advance_pending()
        land_buy(self.ex, self.rpc)
        self.assertIn(MINT, self.ex.state.open)

    def test_landed_buy_measurements(self):
        p = signal_buy(self.ex, self.clock)
        self.clock.t += 1_500
        got = int(p["q_tokens"] * 0.98)
        land_buy(self.ex, self.rpc, tok=got)
        row = [r for r in fills(self.conf) if r["kind"] == "buy"][-1]
        self.assertTrue(row["landed"])
        self.assertEqual(row["mode"], "live")
        self.assertEqual(row["tokens_received"], got)
        self.assertEqual(row["entry_vs_quote_bps"], round((got - p["q_tokens"]) * 1e4 / p["q_tokens"], 2))
        self.assertEqual(row["sol_spent_lamports"], 50_000_000 + 500_000 + RENT)
        self.assertEqual((row["base_fee_lamports"], row["priority_fee_lamports"]), (5_000, 495_000))
        self.assertEqual(row["rent_charged_lamports"], RENT)
        self.assertEqual(row["landed_slot"], 2_000)
        self.assertEqual(row["slots_between"], 2_000 - 77)
        self.assertEqual(row["ms_send_to_confirm"], 1_500)
        self.assertIn("pool_fee_est_lamports", row)
        self.assertEqual(self.ex.state.open[MINT]["tokens"], got)

    def test_balance_guard(self):
        self.rpc.balance = 50_000_000 + 20_000_000 - 1
        self.assertIsNone(signal_buy(self.ex, self.clock))
        self.assertEqual(fills(self.conf)[-1]["reason"], "balance_guard")
        self.assertEqual((self.ex.state.attempts, len(self.rpc.sent)), (0, 0))
        self.rpc.balance = 50_000_000 + 20_000_000
        self.assertIsNotNone(signal_buy(self.ex, self.clock))

    def test_config_cannot_lower_balance_buffer(self):
        ex, *_ = make_live(self.tmp, min_balance_buffer_lamports=1)
        self.assertEqual(ex.balance_buffer, pl.MIN_BALANCE_BUFFER)


class LimitsLiveTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)
        self.ex, self.rpc, self.clock, self.kp, self.conf = make_live(self.tmp)

    def tearDown(self):
        self._d.cleanup()

    def reason(self):
        return fills(self.conf)[-1]["reason"]

    def test_each_limit_halts_live(self):
        self.ex.state.attempts = 30
        self.assertIsNone(signal_buy(self.ex, self.clock))
        self.assertEqual(self.reason(), "limit:max_attempts")
        self.ex.state.attempts = 0
        self.ex.state.realized_lamports = -250_000_000
        signal_buy(self.ex, self.clock)
        self.assertEqual(self.reason(), "limit:loss_cap")
        self.ex.state.realized_lamports = 0
        self.ex.state.first_attempt_ms = T0 - 4 * 86_400_000
        signal_buy(self.ex, self.clock)
        self.assertEqual(self.reason(), "limit:max_days")
        self.ex.state.first_attempt_ms = None
        (self.tmp / "STOP").write_text("")
        signal_buy(self.ex, self.clock)
        self.assertEqual(self.reason(), "limit:stop_file")
        self.assertEqual(len(self.rpc.sent), 0)

    def test_max_open_counts_pending_buys(self):
        self.ex.state.pending = {f"m{i}": {"kind": "buy"} for i in range(2)}
        self.ex.state.open = {"x": {}}
        signal_buy(self.ex, self.clock)
        self.assertEqual(self.reason(), "limit:max_open")

    def test_stop_file_keeps_rebroadcast_and_exits_running(self):
        signal_buy(self.ex, self.clock)
        (self.tmp / "STOP").write_text("")
        self.clock.t += 5_000
        self.ex.advance_pending()
        self.assertEqual(len(self.rpc.sent), 2)  # in-flight buy is still rebroadcast
        land_buy(self.ex, self.rpc)
        self.assertIn(MINT, self.ex.state.open)
        self.clock.t += 31 * 60_000
        self.ex.poll_positions()  # exit still starts under STOP
        self.assertEqual(self.ex.state.pending[MINT]["kind"], "sell")
        self.clock.t += 3_000
        n = len(self.rpc.sent)
        self.ex.advance_pending()
        self.assertEqual(len(self.rpc.sent), n + 1)  # in-flight sell is rebroadcast
        self.ex.handle_signal(mk_sig(self.ex, mint="11111111111111111111111111111112", t=self.clock()))
        self.assertEqual(self.reason(), "limit:stop_file")

    def test_halt_file_freezes_everything(self):
        signal_buy(self.ex, self.clock)
        land_buy(self.ex, self.rpc)
        self.clock.t += 31 * 60_000
        (self.tmp / "HALT").write_text("")
        n = len(self.rpc.sent)
        self.ex.poll_positions()
        self.assertEqual((len(self.rpc.sent), MINT in self.ex.state.pending), (n, False))  # no sell
        self.ex.handle_signal(mk_sig(self.ex, mint="11111111111111111111111111111112", t=self.clock()))
        self.assertEqual(self.reason(), "limit:halt_file")
        (self.tmp / "HALT").unlink()
        self.ex.poll_positions()
        self.assertEqual(self.ex.state.pending[MINT]["kind"], "sell")
        (self.tmp / "HALT").write_text("")
        self.clock.t += 5_000
        n = len(self.rpc.sent)
        self.ex.advance_pending()
        self.assertEqual(len(self.rpc.sent), n)  # no rebroadcast under HALT

    def test_size_and_priority_clamped(self):
        ex, *_ = make_live(self.tmp, size_lamports=10**12, priority_lamports=10**9)
        self.assertEqual((ex.limits.size_lamports, ex.limits.priority_lamports), (50_000_000, 500_000))


class ExitFlowTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)
        self.ex, self.rpc, self.clock, self.kp, self.conf = make_live(self.tmp)
        self.pos = open_position(self.ex, self.rpc, self.clock)

    def tearDown(self):
        self._d.cleanup()

    def trigger_exit(self):
        self.clock.t += 31 * 60_000
        self.ex.poll_positions()
        return self.ex.state.pending.get(MINT)

    def test_sell_actual_balance_closes_atas_and_pnl_from_metas(self):
        self.rpc.token_balance = self.pos["tokens"] - 7  # real balance differs from the booked one
        p = self.trigger_exit()
        self.assertEqual(p["kind"], "sell")
        self.assertEqual(p["tokens"], self.pos["tokens"] - 7)
        t = VersionedTransaction.from_bytes(base64.b64decode(self.rpc.sent[-1][1]))
        progs = [str(t.message.account_keys[i.program_id_index]) for i in t.message.instructions]
        self.assertEqual(progs[-1], str(tx.TOKEN_PROGRAM))  # close token ATA is last
        self.assertEqual(progs.count(str(tx.TOKEN_PROGRAM)), 2)  # WSOL close + token ATA close
        self.assertEqual(p["min_out"], tx.min_out_with_slippage(p["q_out"], self.ex.slip_bps))
        buy_cost = self.pos["buy_cost_lamports"]
        land_sell(self.ex, self.rpc, proceeds=49_000_000)
        row = [r for r in fills(self.conf) if r["kind"] == "sell"][-1]
        self.assertTrue(row["landed"])
        self.assertEqual(row["sol_received_lamports"], 49_000_000)
        self.assertEqual(row["rent_refunded_lamports"], RENT)
        self.assertEqual(row["pnl_lamports"], (49_000_000 - 505_000 + RENT) - buy_cost)
        self.assertEqual(self.ex.state.realized_lamports, row["pnl_lamports"])
        self.assertEqual(row["exit_vs_quote_bps"], round((49_000_000 - p["q_out"]) * 1e4 / p["q_out"], 2))
        self.assertNotIn(MINT, self.ex.state.open)

    def test_sell_retry_fresh_tx_then_stuck_halts_buys(self):
        self.trigger_exit()
        sigs = []
        err = {"InstructionError": [3, {"Custom": 6004}]}
        for i in range(self.ex.sell_retries):
            sigs.append(self.ex.state.pending[MINT]["signature"])
            land_sell(self.ex, self.rpc, proceeds=0, err=err)
            self.assertNotIn(MINT, self.ex.state.pending)
            if i < self.ex.sell_retries - 1:
                self.assertFalse(self.ex.sell_stuck())
                self.ex.poll_positions()
        self.assertEqual(len(set(sigs)), len(sigs))
        self.assertTrue(self.ex.sell_stuck())
        self.assertEqual(self.ex.state.open[MINT]["exit_reason"], "time_stop")
        self.assertTrue(any(r["kind"] == "alert" and r["alert"] == "sell_stuck" for r in fills(self.conf)))
        other = "11111111111111111111111111111112"  # new buys halted
        self.ex.handle_signal(mk_sig(self.ex, mint=other, t=self.clock()))
        self.assertEqual(fills(self.conf)[-1]["reason"], "limit:sell_stuck")
        n = len(self.rpc.sent)  # no retry before 30 s, retry after
        self.clock.t += 29_000
        self.ex.poll_positions()
        self.assertEqual(len(self.rpc.sent), n)
        self.clock.t += 1_001
        self.ex.poll_positions()
        self.assertEqual(len(self.rpc.sent), n + 1)
        land_sell(self.ex, self.rpc, proceeds=40_000_000)  # success clears the stuck state
        self.assertFalse(self.ex.sell_stuck())
        row = [r for r in fills(self.conf) if r["kind"] == "sell"][-1]
        self.assertEqual(row["failed_attempt_cost_lamports"], 5 * 505_000)
        self.assertEqual(self.ex.state.realized_lamports, row["pnl_lamports"])

    def test_sell_expiry_counts_as_failed_attempt(self):
        self.trigger_exit()
        self.rpc.height = LVBH + 1
        self.ex.advance_pending()
        self.clock.t += pl.EXPIRY_RECHECK_MS
        self.ex.advance_pending()
        self.assertNotIn(MINT, self.ex.state.pending)
        self.assertEqual(self.ex.state.open[MINT]["sell_attempts"], 1)
        self.assertEqual(self.ex.state.open[MINT]["tokens"], self.pos["tokens"])  # still held

    def test_sell_restart_resumes_pending(self):
        p = self.trigger_exit()
        ex2 = pl.LiveExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock)
        self.assertEqual(ex2.state.pending[MINT]["signature"], p["signature"])
        n = len(self.rpc.sent)
        ex2.poll_positions()
        self.assertEqual(len(self.rpc.sent), n)  # already pending: no second sell


class ClassifyTests(unittest.TestCase):
    def test_classes(self):
        c = pl.classify_failure
        self.assertEqual(c({"InstructionError": [4, {"Custom": 6004}]}), "slippage_exceeded")
        self.assertEqual(c({"InstructionError": [4, {"Custom": 9999}]}, ["Program log: ExceededSlippage. Slippage: x"]), "slippage_exceeded")
        self.assertEqual(c("InsufficientFundsForFee"), "insufficient_funds")
        self.assertEqual(c({"InstructionError": [2, "InsufficientFunds"]}), "insufficient_funds")
        self.assertEqual(c({"InstructionError": [2, {"Custom": 1}]}, ["Program log: Error: insufficient funds"]), "insufficient_funds")
        self.assertEqual(c({"InstructionError": [2, {"Custom": 1}]}), "other")
        self.assertEqual(c("BlockhashNotFound"), "other")
        self.assertEqual(c(None), "other")

    def test_failed_buy_costs_only_the_fee_and_is_booked(self):
        with tempfile.TemporaryDirectory() as d:
            ex, rpc, clock, kp, conf = make_live(Path(d))
            p = signal_buy(ex, clock)
            err = {"InstructionError": [4, {"Custom": 6004}]}
            rpc.statuses[p["signature"]] = {"slot": 5, "confirmationStatus": "confirmed", "err": err}
            rpc.txs[p["signature"]] = meta_result(ex, MINT, delta=-500_000, fee=500_000, err=err)
            ex.advance_pending()
            row = [r for r in fills(conf) if r["kind"] == "buy"][-1]
            self.assertEqual((row["landed"], row["fail_class"], row["cost_lamports"]), (False, "slippage_exceeded", 500_000))
            self.assertEqual(ex.state.realized_lamports, -500_000)
            self.assertEqual(ex.state.open, {})


class NoKeyLeakTests(unittest.TestCase):
    def test_no_key_material_anywhere(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                ex, rpc, clock, kp, conf = make_live(tmp)
                kp_path = tmp / "k.key"
                kp_path.write_text(json.dumps(list(bytes(kp))))
                os.chmod(kp_path, 0o400)
                loaded = pl.load_probe_key(str(kp_path), verify_chain=False)
                ex._kp = loaded
                ex.user = loaded.pubkey()
                open_position(ex, rpc, clock)
                rpc.send_fails = True
                clock.t += 31 * 60_000
                ex.poll_positions()
                print(repr(ex), str(ex.limits))
                print(pe.status_report(conf))
            secret = bytes(loaded)
            needles = [loaded.to_base58_string() if hasattr(loaded, "to_base58_string") else str(loaded), base64.b64encode(secret).decode(),
                       base64.b64encode(secret[:32]).decode(), json.dumps(list(secret)), secret.hex(), base64.b64encode(secret[32:]).decode()]
            blobs = [buf.getvalue()] + [p.read_text() for p in tmp.glob("*.json*")]
            self.assertGreaterEqual(len(blobs), 3)
            for blob in blobs:
                for n in needles:
                    self.assertNotIn(n, blob)
            self.assertIn(str(loaded.pubkey()), buf.getvalue())


class StatusTests(unittest.TestCase):
    def test_status_prints_counters_without_key_or_url(self):
        with tempfile.TemporaryDirectory() as d:
            ex, rpc, clock, kp, conf = make_live(Path(d))
            open_position(ex, rpc, clock)
            out = pe.status_report(conf)
            self.assertIn("[live] attempts=1/30", out)
            self.assertIn(MINT, out)
            self.assertIn("realized_sol=0.000000", out)
            self.assertNotIn("http", out)
            cp = Path(d) / "c.json"
            cp.write_text(json.dumps(conf))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), mock.patch.object(pe.sim, "load_rpc_url", side_effect=AssertionError("no url")):
                self.assertEqual(pe.main(["--config", str(cp), "--status"]), 0)
            self.assertIn("[live] attempts=1/30", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
