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
from tools.test_probe_executor import POOL_B64 as FX_POOL, MINT, T0, Clock, FakeRpc, fills, sig as mk_sig

POOL_OK = None  # the recorded pool is the canonical pool of MINT with its real vaults (Token-2022 mint)
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
    sig_ = (ex.state.pending.get(mint) or {}).get("signature")
    return {"slot": slot, "transaction": {"signatures": [sig_], "message": {"accountKeys": [str(ex.user), ata]}},
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
            (Path(d) / "decisions.jsonl").write_text("")
            conf = {"signals_dir": d, "state_dir": d, "fill_log": f"{d}/f.jsonl", "mode": "live"}
            cp = Path(d) / "c.json"
            cp.write_text(json.dumps(conf))
            with mock.patch.object(pl, "run_live", return_value=0) as rl, mock.patch.object(pe, "rpc_env_problem", return_value=None), mock.patch.object(pe.sim, "load_rpc_url", return_value="http://x"), \
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
    def write(self, d, kp, mode=0o400):
        p = Path(d) / pl.CREDENTIAL_NAME
        p.write_text(json.dumps(list(bytes(kp))))
        os.chmod(p, mode)
        return str(p)

    def test_live_refuses_without_credentials_directory(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            with self.assertRaises(SystemExit) as cm:
                pl.load_probe_key()
            self.assertIn("CREDENTIALS_DIRECTORY", str(cm.exception))
            with self.assertRaises(SystemExit):
                pl.credential_path()
        with mock.patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": ""}):
            with self.assertRaises(SystemExit):
                pl.credential_path()

    def test_loads_from_credentials_directory(self):
        kp = Keypair()
        with tempfile.TemporaryDirectory() as d:
            self.write(d, kp)
            with mock.patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": d}):
                self.assertEqual(pl.load_probe_key().pubkey(), kp.pubkey())

    def test_missing_malformed_and_symlink_refused_without_leaking(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": d}):
            with self.assertRaises(SystemExit):
                pl.load_probe_key()  # file missing
            p = Path(d) / pl.CREDENTIAL_NAME
            p.write_text("[1,2,3]")
            with self.assertRaises(SystemExit) as cm:
                pl.load_probe_key()
            self.assertNotIn("1,2,3", str(cm.exception))
            p.unlink()
            real = self.write(d, Keypair())
            os.rename(real, Path(d) / "real")
            p.symlink_to(Path(d) / "real")
            with self.assertRaises(SystemExit):
                pl.load_probe_key()

    def test_no_path_override_in_live(self):
        with mock.patch.object(pl, "harden_process"), mock.patch.object(pl, "load_probe_key", side_effect=AssertionError("must not load")):
            with self.assertRaises(SystemExit) as cm:
                pl.run_live({"mode": "live", "key_path": "/somewhere/else.json"}, mock.Mock(env_file="x"), 5.0)
        self.assertIn("no key path override", str(cm.exception))

    def test_unit_and_dropin_text(self):
        root = Path(__file__).parent.parent / "scripts/mal-fast"
        drop = (root / "mal-probe-executor-live.conf").read_text()
        self.assertIn("LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json", drop)
        self.assertNotIn("LoadCredential", (root / "mal-probe-executor.service").read_text())
        self.assertNotIn("key_path", (root / "probe-executor-live.json").read_text())
        self.assertNotIn("probe-wallet.json", (root / "probe-executor.json").read_text())

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

        def key(*_a):
            order.append("key")
            raise SystemExit("stop")

        with mock.patch.object(pl, "harden_process", lambda: order.append("harden")), mock.patch.object(pl, "load_probe_key", side_effect=key), \
                mock.patch.dict(os.environ, {"HELIUS_API_KEY": "k"}):
            with self.assertRaises(SystemExit):
                pl.run_live({"mode": "live"}, mock.Mock(env_file="x"), 5.0)
        self.assertEqual(order, ["harden", "key"])

    def test_live_fails_closed_without_rpc_key_and_never_reads_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            envf = Path(d) / "helius.env"
            envf.write_text("HELIUS_API_KEY=from-file\n")
            env = {k: v for k, v in os.environ.items() if k != "HELIUS_API_KEY"}
            with mock.patch.object(pl, "harden_process"), mock.patch.object(pl, "load_probe_key") as lk, \
                    mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
                rc = pl.run_live({"mode": "live"}, mock.Mock(env_file=str(envf)), 5.0)
            self.assertEqual(rc, 2)
            self.assertIn("ALERT startup_refused rpc_key_missing", out.getvalue())
            lk.assert_not_called()  # before the wallet key loads
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit):
                    pl.sim.load_rpc_url(None, str(envf), use_env_file=False)  # no fallback file
                self.assertIn("from-file", pl.sim.load_rpc_url(None, str(envf)))  # dry run keeps the fallback


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
        self.assertIn(MINT, self.ex.state.pending)  # first sighting: look again, twice, 5 s apart
        n = len(self.rpc.sent)
        self.clock.t += pl.EXPIRY_RECHECK_MS
        self.ex.advance_pending()
        self.assertIn(MINT, self.ex.state.pending)
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
        self.clock.t += pl.EXPIRY_RECHECK_MS
        self.ex.advance_pending()  # one recheck done, still empty
        land_buy(self.ex, self.rpc)  # lands late: becomes an open position, not an expired buy
        self.assertIn(MINT, self.ex.state.open)
        self.assertEqual([r for r in fills(self.conf) if r["kind"] == "buy"][-1]["landed"], True)

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
        self.assertEqual(progs[-1], str(tx.TOKEN_2022_PROGRAM))  # close of the (Token-2022) token ATA is last
        self.assertEqual((progs.count(str(tx.TOKEN_PROGRAM)), progs.count(str(tx.TOKEN_2022_PROGRAM))), (1, 1))  # WSOL close, token ATA close
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
        for _ in range(3):
            self.ex.advance_pending()
            self.clock.t += pl.EXPIRY_RECHECK_MS
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
                loaded = pl.load_probe_key(str(kp_path))
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


def sell_stuck_helper(ex, rpc, clock, n):
    """Fail n landed sells in a row, advancing the clock past any backoff each time."""
    sent = 0
    for _ in range(n):
        clock.t += 11 * 60_000
        ex.poll_positions()
        if MINT in ex.state.pending:
            sent += 1
            land_sell(ex, rpc, proceeds=0, err={"InstructionError": [3, {"Custom": 6004}]})
    return sent


class HighFixTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)
        self.ex, self.rpc, self.clock, self.kp, self.conf = make_live(self.tmp)

    def tearDown(self):
        self._d.cleanup()

    # H1
    def test_failed_sell_fees_hit_realized_immediately(self):
        pos = open_position(self.ex, self.rpc, self.clock)
        self.clock.t += 31 * 60_000
        self.ex.poll_positions()
        land_sell(self.ex, self.rpc, proceeds=0, err={"InstructionError": [3, {"Custom": 6004}]})
        self.assertEqual(self.ex.state.realized_lamports, -505_000)

    def test_100_failed_sells_capped_and_abandoned(self):
        open_position(self.ex, self.rpc, self.clock)
        self.clock.t += 31 * 60_000
        sent = sell_stuck_helper(self.ex, self.rpc, self.clock, 100)
        self.assertEqual(sent, pl.SELL_MAX_ATTEMPTS)
        self.assertEqual(self.ex.state.realized_lamports, -pl.SELL_MAX_ATTEMPTS * 505_000)
        self.assertLess(-self.ex.state.realized_lamports, self.ex.limits.loss_cap_lamports)
        self.assertTrue(self.ex.state.open[MINT]["abandoned"])
        self.assertTrue(any(r.get("alert") == "sell_abandoned" for r in fills(self.conf)))
        self.ex.handle_signal(mk_sig(self.ex, mint="11111111111111111111111111111112", t=self.clock()))
        self.assertEqual(fills(self.conf)[-1]["reason"], "limit:sell_stuck")
        self.assertIn("abandoned=True", pe.status_report(self.conf))

    def test_sell_attempt_cap_cannot_be_raised_and_backoff_doubles(self):
        ex, *_ = make_live(self.tmp / "x" if (self.tmp / "x").mkdir() is None else self.tmp, sell_max_attempts=999)
        self.assertEqual(ex.sell_max_attempts, pl.SELL_MAX_ATTEMPTS)
        open_position(self.ex, self.rpc, self.clock)
        self.clock.t += 31 * 60_000
        self.ex.poll_positions()
        for i in range(self.ex.sell_retries):
            land_sell(self.ex, self.rpc, proceeds=0, err="X")
            if i < self.ex.sell_retries - 1:
                self.ex.poll_positions()
        for k in range(3):  # waits 30 s, 60 s, 120 s
            wait = 30_000 * 2 ** k
            n = len(self.rpc.sent)
            self.clock.t += wait - 1
            self.ex.poll_positions()
            self.assertEqual(len(self.rpc.sent), n)
            self.clock.t += 1
            self.ex.poll_positions()
            self.assertEqual(len(self.rpc.sent), n + 1)
            land_sell(self.ex, self.rpc, proceeds=0, err="X")

    def test_retry_priority_never_above_cap(self):
        open_position(self.ex, self.rpc, self.clock)
        self.clock.t += 31 * 60_000
        self.ex.poll_positions()
        for _ in range(3):
            land_sell(self.ex, self.rpc, proceeds=0, err="X")
            self.clock.t += 11 * 60_000
            self.ex.poll_positions()
        for _, b64 in self.rpc.sent:
            t = VersionedTransaction.from_bytes(base64.b64decode(b64))
            ixs = [bytes(i.data) for i in t.message.instructions if t.message.account_keys[i.program_id_index] == pl.COMPUTE_BUDGET_PROGRAM]
            cu = int.from_bytes(ixs[0][1:], "little")
            price = int.from_bytes(ixs[1][1:], "little")
            self.assertLessEqual(price * cu // 1_000_000, 500_000 + 1)

    # H2
    def test_zero_token_row_in_meta_is_a_realized_loss(self):
        p = signal_buy(self.ex, self.clock)
        spend, fee = p["spend"], 500_000
        self.rpc.statuses[p["signature"]] = {"slot": 5, "confirmationStatus": "confirmed", "err": None}
        res = meta_result(self.ex, MINT, delta=-(spend + fee + RENT), fee=fee, tok_delta=1, ata_post=RENT)
        res["meta"]["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "0"
        self.rpc.txs[p["signature"]] = res
        self.ex.advance_pending()
        self.assertEqual(self.ex.state.open, {})
        self.assertEqual(self.ex.state.realized_lamports, -(spend + fee + RENT))
        self.assertTrue(any(r.get("alert") == "buy_landed_zero_tokens" for r in fills(self.conf)))

    def test_missing_owner_is_unknown_not_zero_and_ata_is_reread(self):
        p = signal_buy(self.ex, self.clock)
        spend, fee = p["spend"], 500_000
        self.rpc.statuses[p["signature"]] = {"slot": 5, "confirmationStatus": "confirmed", "err": None}
        res = meta_result(self.ex, MINT, delta=-(spend + fee + RENT), fee=fee, tok_delta=1234, ata_post=RENT)
        for r in res["meta"]["postTokenBalances"]:
            r.pop("owner")
            r.pop("accountIndex")  # nothing identifies our account: unknown
        self.rpc.txs[p["signature"]] = res
        self.rpc.token_balance = 777
        self.ex.advance_pending()
        self.assertEqual(self.ex.state.open[MINT]["tokens"], 777)
        self.assertEqual([r for r in fills(self.conf) if r["kind"] == "buy"][-1]["tokens_source"], "ata_balance")
        self.assertEqual(self.ex.state.realized_lamports, 0)

    def test_still_unknown_opens_flagged_and_sell_reads_ata(self):
        p = signal_buy(self.ex, self.clock)
        spend, fee = p["spend"], 500_000
        self.rpc.statuses[p["signature"]] = {"slot": 5, "confirmationStatus": "confirmed", "err": None}
        res = meta_result(self.ex, MINT, delta=-(spend + fee + RENT), fee=fee, tok_delta=1234, ata_post=RENT)
        del res["meta"]["postTokenBalances"]
        self.rpc.txs[p["signature"]] = res
        with mock.patch.object(self.ex, "_ata_balance", return_value=None):
            self.ex.advance_pending()
        pos = self.ex.state.open[MINT]
        self.assertTrue(pos["balance_pending"])
        self.rpc.token_balance = 10**9
        self.clock.t += 31 * 60_000
        self.ex.poll_positions()
        self.assertEqual(self.ex.state.pending[MINT]["tokens"], 10**9)

    def test_meta_missing_fallback_after_60s(self):
        p = signal_buy(self.ex, self.clock)
        self.rpc.statuses[p["signature"]] = {"slot": 5, "confirmationStatus": "confirmed", "err": None}
        self.rpc.token_balance = 999
        self.ex.advance_pending()
        self.assertIn(MINT, self.ex.state.pending)
        self.clock.t += pl.META_FALLBACK_MS
        self.ex.advance_pending()
        self.assertEqual(self.ex.state.open[MINT]["tokens"], 999)
        self.assertTrue([r for r in fills(self.conf) if r["kind"] == "buy"][-1]["estimated"])

    # H3
    def test_hostile_mint_owner_refused(self):
        self.rpc.mint_owner = str(Keypair().pubkey())  # random program
        self.assertIsNone(signal_buy(self.ex, self.clock))
        self.assertIn("unsafe_tx", fills(self.conf)[-1]["reason"])
        self.assertEqual((len(self.rpc.sent), self.ex.state.attempts, self.ex.state.pending), (0, 0, {}))

    def test_token_program_that_does_not_match_the_vaults_refused(self):
        self.rpc.mint_owner = str(tx.TOKEN_PROGRAM)  # the recorded pool's vaults are Token-2022 ATAs: mismatch -> refuse
        self.assertIsNone(signal_buy(self.ex, self.clock))
        self.assertIn("unsafe_tx", fills(self.conf)[-1]["reason"])

    def test_pool_not_canonical_or_wrong_mints_skipped(self):
        import dataclasses

        snap, _pool, _e = self.ex._snapshot(MINT)
        self.assertIsNotNone(snap)
        # a pool account whose base mint is not the requested mint: skipped, never traded
        d = bytearray(base64.b64decode(FX_POOL))
        d[43:75] = bytes(Keypair().pubkey())
        self.rpc.pool_b64 = base64.b64encode(bytes(d)).decode()
        self.assertEqual(self.ex._snapshot(MINT)[2], "no_canonical_pool")
        self.assertIsNone(signal_buy(self.ex, self.clock))
        self.assertEqual(fills(self.conf)[-1]["reason"], "no_canonical_pool")
        d = bytearray(base64.b64decode(FX_POOL))
        d[75:107] = bytes(Keypair().pubkey())  # quote mint not WSOL
        self.rpc.pool_b64 = base64.b64encode(bytes(d)).decode()
        self.assertEqual(self.ex._snapshot(MINT)[2], "no_canonical_pool")

    def test_mismatched_base_mint_refused(self):
        other = str(Keypair().pubkey())
        self.ex.handle_signal(mk_sig(self.ex, mint=other, t=self.clock()))
        # the pool for `other` does not exist on chain (pool_v2 differs) in reality; the hostile RPC returns our fixture pool
        self.assertNotIn(other, self.ex.state.pending)
        self.assertEqual(len(self.rpc.sent), 0)

    def test_validator_rejects_foreign_program_signer_and_destinations(self):
        from solders.instruction import AccountMeta, Instruction
        from solders.message import Message
        from solders.pubkey import Pubkey

        p = signal_buy(self.ex, self.clock)
        snap, _pool, _e = self.ex._snapshot(MINT)
        mint = Pubkey.from_string(MINT)
        good = tx.build_buy(snap.ps, self.ex.user, 50_000_000, 1500, 1000, priority_total_lamports=500_000)
        pl.validate_message(good, snap.ps, mint, self.ex.user, 500_000)
        evil = Pubkey.from_string("11111111111111111111111111111112")
        ixs = list(tx.buy_instructions(snap.ps, self.ex.user, 50_000_000, 1000, 1500, priority_total_lamports=500_000))
        cases = {
            "program": ixs + [Instruction(evil, b"", [AccountMeta(self.ex.user, True, True)])],
            "transfer_dest": [*ixs[:4], tx.transfer(tx.TransferParams(from_pubkey=self.ex.user, to_pubkey=evil, lamports=5)), *ixs[5:]],
            "second_signer": ixs + [Instruction(tx.TOKEN_PROGRAM, b"\x09", [AccountMeta(evil, True, True)])],
            "close_dest": ixs[:-1] + [tx.close_account(tx.ata(self.ex.user, tx.WSOL_MINT, tx.TOKEN_PROGRAM), evil, self.ex.user)],
        }
        for name, seq in cases.items():
            with self.assertRaises(pl.UnsafeTx, msg=name):
                pl.validate_message(Message.new_with_blockhash(seq, self.ex.user, tx.Hash.default()), snap.ps, mint, self.ex.user, 500_000)
        with self.assertRaises(pl.UnsafeTx):  # priority above the cap
            pl.validate_message(tx.build_buy(snap.ps, self.ex.user, 50_000_000, 1500, 1000, priority_total_lamports=600_000), snap.ps, mint, self.ex.user, 500_000)
        with self.assertRaises(pl.UnsafeTx):  # quote mint not WSOL
            import dataclasses
            pl.validate_message(good, dataclasses.replace(snap.ps, quote_mint=evil), mint, self.ex.user, 500_000)
        with self.assertRaises(pl.UnsafeTx):  # base mint != requested
            pl.validate_message(good, snap.ps, evil, self.ex.user, 500_000)

    # mediums
    def test_malformed_gettransaction_does_not_crash_and_retries(self):
        p = signal_buy(self.ex, self.clock)
        self.rpc.statuses[p["signature"]] = {"slot": 5, "confirmationStatus": "confirmed", "err": None}
        for bad in ({"meta": {"fee": 1}, "transaction": {"signatures": [p["signature"]], "message": {"accountKeys": []}}},
                    {"meta": {"fee": 1}, "transaction": {"signatures": ["other"], "message": {"accountKeys": []}}},
                    {"meta": {}, "transaction": None}):
            self.rpc.txs[p["signature"]] = bad
            self.ex.advance_pending()
            self.assertIn(MINT, self.ex.state.pending)
        self.assertEqual(sum(1 for r in fills(self.conf) if r.get("alert") == "meta_malformed"), 1)
        self.rpc.txs[p["signature"]] = meta_result(self.ex, MINT, delta=-52_500_000, fee=500_000, tok_delta=p["q_tokens"], ata_post=RENT)
        self.ex.advance_pending()
        self.assertIn(MINT, self.ex.state.open)

    def test_mint_never_rebought_even_after_close_and_restart(self):
        open_position(self.ex, self.rpc, self.clock)
        self.clock.t += 31 * 60_000
        self.ex.poll_positions()
        land_sell(self.ex, self.rpc, proceeds=50_000_000)
        self.assertEqual(self.ex.state.open, {})
        ex2 = pl.LiveExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock)
        ex2.handle_signal(mk_sig(ex2, t=self.clock()))
        self.assertEqual(fills(self.conf)[-1]["reason"], "already_bought")
        self.assertEqual(ex2.state.attempts, 1)

    def test_clock_backwards_halts_buys(self):
        self.ex.step()
        self.clock.t -= 61_000
        self.ex.handle_signal(mk_sig(self.ex, t=self.clock()))
        self.assertEqual(fills(self.conf)[-1]["reason"], "limit:clock_backwards")
        ex2 = pl.LiveExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock)  # persisted: survives a restart
        ex2.handle_signal(mk_sig(ex2, t=self.clock()))
        self.assertEqual(fills(self.conf)[-1]["reason"], "limit:clock_backwards")
        self.clock.t += 61_000 + 1
        self.ex.handle_signal(mk_sig(self.ex, t=self.clock()))
        self.assertIn(MINT, self.ex.state.pending)

    def test_status_shows_open_exposure(self):
        open_position(self.ex, self.rpc, self.clock)
        self.assertIn("open_exposure_sol=0.050000", pe.status_report(self.conf))

    def test_dry_run_row_reports_live_whitelist_result(self):
        from tools.test_probe_executor import make as make_dry

        ex, conf = make_dry(self.tmp / "dry" if (self.tmp / "dry").mkdir() is None else self.tmp, rpc=LiveRpc(self.clock))
        ex.handle_signal(mk_sig(ex, t=T0))
        buy = [r for r in fills(conf) if r["kind"] == "buy"][-1]
        self.assertIn("live_validate_err", buy)
        self.assertIsNone(buy["live_validate_err"])



class FastSignalLiveTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)
        self.ex, self.rpc, self.clock, self.kp, self.conf = make_live(self.tmp)

    def tearDown(self):
        self._d.cleanup()

    def _enter(self, **extra):
        from tools.test_probe_executor import enter_row

        dec = self.tmp / "sig" / "decisions.jsonl"
        with dec.open("a") as fh:
            fh.write(enter_row(t=self.clock(), **extra))

    def test_tick_buys_on_the_signal_tick_with_prewarmed_blockhash(self):
        (self.tmp / "sig" / "decisions.jsonl").write_text("")
        self.ex.tick()  # starts at end of file; slow work pre-warms static accounts and the blockhash
        self.assertEqual(self.rpc.fetches, 1)
        self.assertIsNotNone(self.ex.static.gc)
        self.rpc.calls.clear()
        self.clock.t += 100  # slow timer not due
        self._enter(latency={"applied_latency_ms": 250})
        self.assertEqual(self.ex.tick(), 1)
        self.assertIn(MINT, self.ex.state.pending)
        self.assertEqual(self.rpc.fetches, 1)  # no getLatestBlockhash on the buy path
        self.assertNotIn("getLatestBlockhash", self.rpc.calls)
        self.assertLessEqual(self.rpc.calls.count("getAccountInfo") + self.rpc.calls.count("getMultipleAccounts"), 2)
        self.assertEqual(len(self.rpc.sent), 1)

    def test_buy_row_stage_fields_after_landing(self):
        (self.tmp / "sig" / "decisions.jsonl").write_text("")
        self.ex.tick()
        self.clock.t += 10
        self._enter(latency={"applied_latency_ms": 250})
        self.ex.tick()
        p = self.ex.state.pending[MINT]
        for k in ("seen_ms", "state_ms", "state_slot", "built_ms", "first_send_ms"):
            self.assertIsNotNone(p[k], k)
        land_buy(self.ex, self.rpc, slot=2_000)
        row = [r for r in fills(self.conf) if r["kind"] == "buy"][-1]
        self.assertEqual(row["landed_slot"], 2_000)
        self.assertEqual(row["latency"], {"applied_latency_ms": 250})
        self.assertEqual(row["sent_ms"], row["first_send_ms"])
        self.assertLessEqual(row["decision_t_ms"], row["seen_ms"])
        self.assertLessEqual(row["seen_ms"], row["state_ms"])
        self.assertLessEqual(row["state_ms"], row["built_ms"])
        self.assertLessEqual(row["built_ms"], row["sent_ms"])
        self.assertEqual(row["state_slot"], 77)
        self.assertIn("state_to_landed_slots", pe.latency_report(self.conf))

    def test_expired_buy_row_keeps_stage_fields(self):
        p = signal_buy(self.ex, self.clock)
        self.assertIsNotNone(p)
        self.ex._resolve_expired(MINT, self.ex.state.pending[MINT])
        row = [r for r in fills(self.conf) if r["kind"] == "buy"][-1]
        self.assertIn("state_ms", row)
        self.assertIn("sent_ms", row)
        self.assertIsNone(row.get("landed_slot"))

    def test_write_ahead_still_precedes_send_on_the_fast_path(self):
        (self.tmp / "sig" / "decisions.jsonl").write_text("")
        self.ex.tick()
        seen = {}

        def hook(params):
            st = pe.State.load(self.tmp / "state-live.json", "live")
            seen["attempts"], seen["pending"] = st.attempts, list(st.pending)
        self.rpc.send_hook = hook
        self._enter()
        self.ex.tick()
        self.assertEqual(seen, {"attempts": 1, "pending": [MINT]})

    def test_confirm_loop_runs_on_its_own_timer_in_tick(self):
        (self.tmp / "sig" / "decisions.jsonl").write_text("")
        self.ex.tick()
        self._enter()
        self.ex.tick()
        self.rpc.calls.clear()
        self.clock.t += 500
        self.ex.tick()
        self.assertNotIn("getSignatureStatuses", self.rpc.calls)
        self.clock.t += 600
        self.ex.tick()
        self.assertIn("getSignatureStatuses", self.rpc.calls)

    def test_limits_still_enforced_on_fast_path(self):
        (self.tmp / "sig" / "decisions.jsonl").write_text("")
        self.ex.tick()
        (self.tmp / "STOP").write_text("")
        self._enter()
        self.assertEqual(self.ex.tick(), 1)
        self.assertEqual(fills(self.conf)[-1]["reason"], "limit:stop_file")
        self.assertEqual(self.rpc.sent, [])


    def test_signal_hook_inside_ata_read_cannot_open_a_fourth_position(self):
        from tools.test_probe_executor import enter_row

        (self.tmp / "sig" / "decisions.jsonl").write_text("")
        self.ex.signal_tick()
        for i in range(2):  # two other positions already open
            self.ex.state.open[f"OTHER{i}"] = {"mint": f"OTHER{i}", "pool": "p", "t_entry_ms": self.clock(), "tokens": 1,
                                               "net_in": 1, "mark": 1.0, "spend": 1, "base_ata": "a", "base_mint": "m"}
        p = signal_buy(self.ex, self.clock)
        self.assertEqual(len(self.ex.state.open) + len(self.ex.state.pending), 3)
        snaps = []
        orig = self.rpc.__class__.__call__

        def hooked(rpc, method, params):
            if method == "getTokenAccountBalance":  # the limiter's idle hook fires here, as in run_loop
                with (self.tmp / "sig" / "decisions.jsonl").open("a") as fh:
                    fh.write(enter_row(mint="FOURTH", t=self.clock()))
                self.ex.signal_tick()
                st = pe.State.load(self.tmp / "state-live.json", "live")
                snaps.append((MINT in st.pending or MINT in st.open, len(st.open) + len(st.pending)))
            return orig(rpc, method, params)
        self.rpc.__class__.__call__ = hooked
        try:
            self.rpc.statuses[p["signature"]] = {"slot": 2_000, "confirmationStatus": "confirmed", "err": None}
            res = meta_result(self.ex, MINT, delta=-(p["spend"] + 500_000 + RENT), fee=500_000, tok_delta=0, ata_post=RENT)
            res["meta"]["preTokenBalances"] = None  # token delta unknown: forces the ATA balance read
            res["meta"]["postTokenBalances"] = None
            self.rpc.txs[p["signature"]] = res
            self.rpc.token_balance = p["q_tokens"]
            self.ex.advance_pending()
        finally:
            self.rpc.__class__.__call__ = orig
        self.assertTrue(snaps, "the ATA read must have happened")
        self.assertTrue(all(s == (True, 3) for s in snaps))  # saved state always holds the position, never a 4th
        self.assertEqual(len(self.ex.state.open), 3)
        self.assertNotIn("FOURTH", self.ex.state.pending)
        self.ex.signal_tick()  # the next tick picks the row up, and max_open refuses it
        self.assertNotIn("FOURTH", self.ex.state.pending)
        self.assertEqual(fills(self.conf)[-1]["reason"], "limit:max_open")

    def test_ata_read_precedes_pending_delete(self):
        p = signal_buy(self.ex, self.clock)
        seen = []
        orig = self.ex._ata_balance

        def spy(ata):
            seen.append((MINT in self.ex.state.pending, MINT in self.ex.state.open))
            return orig(ata)
        self.ex._ata_balance = spy
        m = dict(slot=2_000, err=None, fee=500_000, sol_delta=-50_000_000, token_delta=None, ata_rent_pre=0, ata_rent_post=RENT, logs=[])
        self.rpc.token_balance = p["q_tokens"]
        self.ex._finish_buy(MINT, p, m)
        self.assertEqual(seen, [(True, False)])
        self.assertIn(MINT, self.ex.state.open)
        self.assertNotIn(MINT, self.ex.state.pending)


class ExitFastPathTests(unittest.TestCase):
    """exit_poll_ms cadence, batched vault snapshot, exit_commitment, buy-meta sell amount."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def fast(self, **cfg):
        ex, rpc, clock, kp, conf = make_live(self.tmp, exit_poll_ms=400, exit_commitment="processed", **cfg)
        (self.tmp / "sig" / "decisions.jsonl").write_text("")
        pos = open_position(ex, rpc, clock)
        ex.tick()  # first slow pass (starts the signal offset, pre-warm, one position poll)
        return ex, rpc, clock, conf, pos

    @staticmethod
    def multis(rpc):
        return rpc.calls.count("getMultipleAccounts")

    def test_clamps(self):
        self.assertEqual(pe.clamp_exit_poll_ms(None, 5000), 5000)
        self.assertEqual(pe.clamp_exit_poll_ms(50, 5000), 200)
        self.assertEqual(pe.clamp_exit_poll_ms(400, 5000), 400)
        self.assertEqual(pe.clamp_exit_poll_ms(99_999, 5000), 5000)
        for bad in (float("nan"), float("inf"), "400", True, [1]):
            self.assertEqual(pe.clamp_exit_poll_ms(bad, 5000), 5000)
        ex = make_live(self.tmp, exit_poll_ms=1, poll_s=2.0)[0]
        self.assertEqual((ex.exit_poll_ms, ex.poll_ms, ex.exit_commitment), (200, 2000, "confirmed"))

    def test_old_config_is_unchanged(self):
        ex, rpc, clock, _kp, _conf = make_live(self.tmp)
        (self.tmp / "sig" / "decisions.jsonl").write_text("")
        open_position(ex, rpc, clock)
        self.assertFalse(ex.fast_exit)
        self.assertEqual((ex.exit_poll_ms, ex.exit_commitment), (ex.poll_ms, ex.commitment))
        ex.tick()
        n = self.multis(rpc)
        for _ in range(10):  # 400 ms steps: nothing before poll_ms
            clock.t += 400
            ex.tick()
        self.assertEqual(self.multis(rpc), n)  # 4000 ms after the last poll: not yet due on the slow clock
        clock.t += 1_000
        ex.tick()
        self.assertEqual(self.multis(rpc), n + 1)  # one full fetch per slow poll

    def test_cadence_fast_when_open_slow_when_none(self):
        ex, rpc, clock, _conf, _pos = self.fast()
        n = self.multis(rpc)
        clock.t += 200
        ex.tick()
        self.assertEqual(self.multis(rpc), n)  # not due
        clock.t += 200
        ex.tick()
        self.assertEqual(self.multis(rpc), n + 1)  # 400 ms after the last position poll
        clock.t += 400
        ex.tick()
        self.assertEqual(self.multis(rpc), n + 2)
        del ex.state.open[MINT]  # nothing open: only the slow clock (pre-warm) runs
        m, g = self.multis(rpc), rpc.calls.count("getAccountInfo")
        for _ in range(5):
            clock.t += 400
            ex.tick()
        self.assertEqual((self.multis(rpc), rpc.calls.count("getAccountInfo")), (m, g))

    def test_pending_position_is_not_polled_fast(self):
        ex, rpc, clock, _conf, _pos = self.fast()
        ex.state.pending[MINT] = {"kind": "sell"}
        n = self.multis(rpc)
        clock.t += 400
        ex.tick()
        self.assertEqual(self.multis(rpc), n)

    def test_batched_one_call_prices_like_full_fetch(self):
        ex, rpc, clock, _conf, _pos = self.fast()
        full = pe.fetch_snapshot(rpc, ex._snapshot(MINT)[1], "confirmed", ex.user, ex.static, clock())
        extra = ["m2", "m3"]
        for m in extra:
            ex._pool_cache[m] = ex._pool_cache[MINT]
        rpc.calls.clear()
        got = ex._batched_exit_snapshots([MINT, *extra])
        self.assertEqual(rpc.calls, ["getMultipleAccounts"])
        self.assertEqual(rpc.multi_params[0], [str(full.ps.base_vault), str(full.ps.quote_vault)] * 3)
        self.assertEqual(rpc.multi_params[1]["commitment"], "processed")
        self.assertEqual(set(got), {MINT, *extra})
        for m in got:
            self.assertEqual(got[m], full)  # same pool state, slot, reserves and V
            self.assertEqual(pe.exit_check(ex.state.open[MINT], got[m], clock()), pe.exit_check(ex.state.open[MINT], full, clock()))

    def test_fallback_to_full_fetch_without_cache_or_after_ttl(self):
        ex, rpc, clock, _conf, pos = self.fast()
        ex._pool_cache.clear()
        rpc.calls.clear()
        self.assertEqual(ex._batched_exit_snapshots([MINT]), {})
        self.assertEqual(rpc.calls, [])  # nothing cached: no batch call at all
        snap, _p, err, kind = ex._exit_snapshot(MINT, {})
        self.assertEqual((err, kind), (None, "full"))
        self.assertEqual(rpc.calls, ["getAccountInfo", "getMultipleAccounts"])
        self.assertEqual(rpc.multi_params[1]["commitment"], "processed")
        self.assertIn(MINT, ex._pool_cache)  # re-cached by the full fetch
        clock.t += pe.STATIC_TTL_MS + 1  # a stale cache is not used
        self.assertEqual(ex._batched_exit_snapshots([MINT]), {})

    def test_batched_parse_problem_or_missing_vault_falls_back(self):
        ex, rpc, clock, _conf, _pos = self.fast()
        orig = rpc.__class__.__call__
        rpc.__class__.__call__ = lambda self, m, p: ({"context": {"slot": 1}, "value": [None, None]} if m == "getMultipleAccounts" and len(p[0]) == 2 else orig(self, m, p))
        try:
            self.assertEqual(ex._batched_exit_snapshots([MINT]), {})
            snap, _p, err, kind = ex._exit_snapshot(MINT, {})
        finally:
            rpc.__class__.__call__ = orig
        self.assertEqual(kind, "full")

    def _crash(self, rpc):
        rpc.quote = rpc.quote // 3  # price collapses: sl

    def test_first_sell_uses_buy_meta_then_retry_uses_rpc(self):
        ex, rpc, clock, conf, pos = self.fast()
        rpc.token_balance = pos["tokens"] - 7  # the RPC would say something else: it must not be asked
        self._crash(rpc)
        clock.t += 400
        rpc.calls.clear()
        ex.tick()
        p = ex.state.pending[MINT]
        self.assertNotIn("getTokenAccountBalance", rpc.calls)
        self.assertEqual(p["tokens"], pos["tokens"])
        self.assertEqual((p["balance_source"], p["exit_snapshot"], p["exit_poll_ms"], p["exit_commitment"]), ("buy_meta", "batched", 400, "processed"))
        self.assertEqual(rpc.calls.count("getMultipleAccounts"), 1)
        land_sell(ex, rpc, proceeds=0, err={"InstructionError": [3, {"Custom": 6004}]})
        row = [r for r in fills(conf) if r["kind"] == "sell"][-1]
        self.assertEqual((row["balance_source"], row["exit_snapshot"], row["exit_poll_ms"], row["exit_commitment"]), ("buy_meta", "batched", 400, "processed"))
        clock.t += 400
        rpc.calls.clear()
        ex.tick()
        p2 = ex.state.pending[MINT]
        self.assertIn("getTokenAccountBalance", rpc.calls)  # retry: balance from the RPC
        self.assertEqual((p2["balance_source"], p2["tokens"]), ("rpc", pos["tokens"] - 7))

    def _fallback(self, bad):
        ex, rpc, clock, conf, pos = self.fast()
        pos["tokens"] = bad
        rpc.token_balance = 123_456
        self._crash(rpc)
        rpc.calls.clear()
        ex.poll_positions()
        self.assertIn("getTokenAccountBalance", rpc.calls)
        self.assertEqual(ex.state.pending[MINT]["balance_source"], "rpc")
        self.assertEqual(ex.state.pending[MINT]["tokens"], 123_456)

    def test_zero_buy_tokens_fall_back_to_rpc(self):
        self._fallback(0)

    def test_balance_pending_position_reads_rpc(self):
        ex, rpc, clock, conf, pos = self.fast()
        pos["balance_pending"] = True
        rpc.token_balance = pos["tokens"] - 3
        self._crash(rpc)
        ex.poll_positions()
        self.assertEqual((ex.state.pending[MINT]["balance_source"], ex.state.pending[MINT]["tokens"]), ("rpc", pos["tokens"] - 3))

    def test_exit_calls_use_limiter_priority(self):
        ex, rpc, clock, conf, pos = self.fast()
        seen = []
        lim = pe.LimitedRpc(rpc, rps=4.0, max_rps=5.0, clock=lambda: 0.0, sleep=lambda s: None)
        inner = lim.rpc
        lim.rpc = lambda m, p: (seen.append((m, lim._prio)), inner(m, p))[1]
        ex.rpc = lim
        self._crash(rpc)
        ex.poll_positions()
        got = dict(seen)
        self.assertGreater(got["getMultipleAccounts"], 0)
        self.assertGreater(got["sendTransaction"], 0)

    def test_old_config_exit_path_is_not_prioritised(self):
        ex = make_live(self.tmp)[0]
        self.assertEqual(type(ex._exit_prio()).__name__, "nullcontext")


    def test_buy_meta_records_ata_pre_amount_and_nonzero_uses_rpc(self):
        ex, rpc, clock, conf, pos = self.fast()
        self.assertEqual(pos["ata_pre_amount"], 0)
        pos["ata_pre_amount"] = 5  # the ATA already held tokens before the buy: buy-meta tokens are not the balance
        rpc.token_balance = pos["tokens"] + 5
        self._crash(rpc)
        rpc.calls.clear()
        ex.poll_positions()
        self.assertIn("getTokenAccountBalance", rpc.calls)
        self.assertEqual((ex.state.pending[MINT]["balance_source"], ex.state.pending[MINT]["tokens"]), ("rpc", pos["tokens"] + 5))

    def test_unknown_ata_pre_amount_uses_rpc(self):
        ex, rpc, clock, conf, pos = self.fast()
        pos.pop("ata_pre_amount")
        self._crash(rpc)
        ex.poll_positions()
        self.assertEqual(ex.state.pending[MINT]["balance_source"], "rpc")

    def test_stale_batch_skips_remaining_positions(self):
        ex, rpc, clock, conf, pos = self.fast()
        ex.state.open["M2"] = {**pos, "mint": "M2"}
        ex._pool_cache["M2"] = ex._pool_cache[MINT]
        rpc.send_hook = lambda params: setattr(clock, "t", clock.t + 1_500)  # the first sell's send is slow
        self._crash(rpc)
        rpc.calls.clear()
        ex.poll_positions()
        self.assertIn(MINT, ex.state.pending)
        self.assertNotIn("M2", ex.state.pending)  # priced from a batch older than 1 s: skipped this tick
        self.assertEqual(rpc.calls.count("getMultipleAccounts"), 1)
        rpc.send_hook = None
        rpc.calls.clear()
        ex.poll_positions()  # next tick re-fetches
        self.assertEqual(rpc.calls.count("getMultipleAccounts"), 1)

    def test_fresh_batch_prices_second_position(self):
        ex, rpc, clock, conf, pos = self.fast()
        ex.state.open["M2"] = {**pos, "mint": "M2"}
        ex._pool_cache["M2"] = ex._pool_cache[MINT]
        batched = ex._batched_exit_snapshots(["M2"])
        self.assertFalse(ex._batch_stale("M2", batched))
        clock.t += pe.BATCH_MAX_AGE_MS + 1
        self.assertTrue(ex._batch_stale("M2", batched))

    def test_batch_fallback_error_logged_once_a_minute(self):
        ex, rpc, clock, conf, pos = self.fast()
        orig = rpc.__class__.__call__

        def boom(self_, m, p):
            if m == "getMultipleAccounts" and len(p[0]) == 2:
                raise pe.RpcError("timeout")
            return orig(self_, m, p)
        rpc.__class__.__call__ = boom
        try:
            for _ in range(3):
                clock.t += 400
                self.assertEqual(ex._batched_exit_snapshots([MINT]), {})
            rows = [r for r in fills(conf) if r["kind"] == "batch_fallback"]
            self.assertEqual([r["label"] for r in rows], ["timeout"])
            clock.t += 60_000
            ex._batched_exit_snapshots([MINT])
            self.assertEqual(len([r for r in fills(conf) if r["kind"] == "batch_fallback"]), 2)
        finally:
            rpc.__class__.__call__ = orig


class ShippedExitConfigTests(unittest.TestCase):
    def test_live_and_dryrun_configs_mirror_the_exit_keys(self):
        d = Path(__file__).resolve().parent.parent / "scripts" / "mal-fast"
        live = json.loads((d / "probe-executor-live.json").read_text())
        dry = json.loads((d / "probe-executor.json").read_text())
        for c in (live, dry):
            self.assertEqual((c["exit_poll_ms"], c["exit_commitment"]), (400, "processed"))
        self.assertLessEqual(live["rps"], pl.LIVE_MAX_RPS)


if __name__ == "__main__":
    unittest.main()
