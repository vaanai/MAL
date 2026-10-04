"""Offline tests for tools/probe_executor.py. A fake Rpc serves a recorded PumpSwap pool; no network."""

from __future__ import annotations

import base64
import json
import re
import tempfile
import unittest
from pathlib import Path

from tools import probe_executor as pe
from tools import pumpswap_tx as tx

FIX = Path(__file__).parent / "fixtures" / "pumpswap"
FX = json.loads((FIX / "buy_exact_quote_in_a.json").read_text())
GC = json.loads((FIX / "global_config.json").read_text())
POOL_B64 = FX["pool_account_b64"]
POOL = tx.parse_pool_account(base64.b64decode(POOL_B64))
MINT = str(POOL["base_mint"])
V = POOL["virtual_quote_reserves"]
T0 = 1_800_000_000_000
BASE0 = 400_000_000 * 10**6
Q0 = 70 * 10**9


def tok_acct(amount: int) -> dict:
    d = bytearray(165)
    d[64:72] = amount.to_bytes(8, "little")
    return {"owner": str(tx.TOKEN_PROGRAM), "data": [base64.b64encode(bytes(d)).decode(), "base64"], "lamports": 2_039_280}


class FakeRpc:
    def __init__(self, quote=Q0, base=BASE0, pool_b64=POOL_B64, sim_err=None, owner=None):
        self.quote, self.base, self.pool_b64, self.sim_err = quote, base, pool_b64, sim_err
        self.owner = owner or str(tx.PUMPSWAP_PROGRAM)
        self.calls: list[str] = []

    def __call__(self, method, params):
        self.calls.append(method)
        if method == "getAccountInfo":
            return {"context": {"slot": 77}, "value": {"owner": self.owner, "data": [self.pool_b64, "base64"], "lamports": 1}}
        if method == "getMultipleAccounts":
            return {"context": {"slot": 77}, "value": [
                {"owner": "x", "data": [GC["data_b64"], "base64"], "lamports": 1},
                {"owner": str(tx.TOKEN_PROGRAM), "data": ["", "base64"], "lamports": 1},
                tok_acct(self.base), tok_acct(self.quote)]}
        if method == "simulateTransaction":
            if self.sim_err:
                return {"value": {"err": self.sim_err, "logs": ["Program x failed: custom program error: 0x1"], "unitsConsumed": 1}}
            return {"value": {"err": None, "unitsConsumed": 90_000, "logs": [], "accounts": [None, tok_acct(123)]}}
        raise AssertionError(method)


def no_v_pool() -> str:
    return base64.b64encode(base64.b64decode(POOL_B64)[:243]).decode()  # no tail after coin_creator: V unreadable


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def make(tmp: Path, rpc=None, clock=None, **cfg):
    conf = {"signals_dir": str(tmp / "sig"), "state_file": str(tmp / "state.json"), "fill_log": str(tmp / "fills.jsonl"),
            "stop_file": str(tmp / "STOP"), **cfg}
    (tmp / "sig").mkdir(exist_ok=True)
    return pe.Executor(rpc or FakeRpc(), conf, now_ms=clock or Clock()), conf


def sig(ex, mint=MINT, t=T0):
    return {"mint": mint, "decision_t_ms": t, "score": 0.9, "trigger": "t", "book": ex.book}


def enter_row(mint=MINT, t=T0, book=pe.DEFAULT_BOOK, ledger="ceiling", action="enter", **extra):
    return json.dumps({"schema": "forward_paper_decision_v1", "book": book, "ledger": ledger, "mint": mint, "creator": "c",
                       "decision_t_ms": t, "trigger": "migrate", "action": action, "reason": None, "score": 0.9,
                       "entry_status": "filled", "latency": None, **extra}, separators=(",", ":")) + "\n"


def fills(conf):
    p = Path(conf["fill_log"])
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class LimitsTests(unittest.TestCase):
    def test_config_cannot_raise_maxima(self):
        lim = pe.Limits.from_config({"max_attempts": 999, "max_open": 50, "loss_cap_lamports": 10**12, "max_days": 30,
                                     "size_lamports": 10**10, "priority_lamports": 10**8})
        for k, cap in pe.DEC019_MAX.items():
            self.assertEqual(getattr(lim, k), cap)

    def test_config_can_lower(self):
        lim = pe.Limits.from_config({"max_attempts": 2, "max_open": 1})
        self.assertEqual((lim.max_attempts, lim.max_open), (2, 1))

    def test_nonpositive_rejected(self):
        with self.assertRaises(ValueError):
            pe.Limits.from_config({"max_attempts": 0})

    def test_each_stop(self):
        lim = pe.Limits()
        self.assertIsNone(pe.check_buy(lim, pe.State(), T0, False))
        self.assertEqual(pe.check_buy(lim, pe.State(), T0, True), "stop_file")
        self.assertEqual(pe.check_buy(lim, pe.State(attempts=30), T0, False), "max_attempts")
        self.assertEqual(pe.check_buy(lim, pe.State(realized_lamports=-250_000_000), T0, False), "loss_cap")
        self.assertIsNone(pe.check_buy(lim, pe.State(realized_lamports=-249_999_999), T0, False))
        st = pe.State(first_attempt_ms=T0)
        self.assertIsNone(pe.check_buy(lim, st, T0 + 4 * 86_400_000 - 1, False))
        self.assertEqual(pe.check_buy(lim, st, T0 + 4 * 86_400_000, False), "max_days")
        self.assertEqual(pe.check_buy(lim, pe.State(open={"a": {}, "b": {}, "c": {}}), T0, False), "max_open")

    def test_sell_only_halted_by_stop_file(self):
        self.assertEqual(pe.check_sell(True), "stop_file")
        self.assertIsNone(pe.check_sell(False))

    def test_state_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            pe.State(attempts=7, first_attempt_ms=5, realized_lamports=-9, open={"m": {"x": 1}}, offset=12, inode=3, started=True).save(p)
            st = pe.State.load(p)
            self.assertEqual((st.attempts, st.first_attempt_ms, st.realized_lamports, st.offset, st.started), (7, 5, -9, 12, True))
            self.assertEqual(st.open, {"m": {"x": 1}})

    def test_executor_restart_cannot_reset_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            ex, conf = make(tmp, max_attempts=1)
            ex.handle_signal(sig(ex))
            self.assertEqual(ex.state.attempts, 1)
            ex2, _ = make(tmp, max_attempts=1)  # restart
            ex2.state.open.clear()
            ex2.handle_signal(sig(ex2))
            self.assertEqual(ex2.state.attempts, 1)
            self.assertEqual(fills(conf)[-1]["reason"], "limit:max_attempts")

    def test_stop_file_checked_before_buy(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d))
            Path(conf["stop_file"]).write_text("")
            ex.handle_signal(sig(ex))
            self.assertEqual(fills(conf)[0]["reason"], "limit:stop_file")
            self.assertEqual(ex.rpc.calls, [])

    def test_stop_file_halts_sell(self):
        with tempfile.TemporaryDirectory() as d:
            rpc = FakeRpc()
            ex, conf = make(Path(d), rpc=rpc)
            ex.handle_signal(sig(ex))
            Path(conf["stop_file"]).write_text("")
            rpc.calls.clear()
            ex.poll_positions()
            self.assertEqual(rpc.calls, [])
            self.assertIn(MINT, ex.state.open)

    def test_loss_cap_stops_new_buys(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d), loss_cap_lamports=10)
            ex.state.realized_lamports = -10
            ex.handle_signal(sig(ex))
            self.assertEqual(fills(conf)[0]["reason"], "limit:loss_cap")


class TailerTests(unittest.TestCase):
    def test_enter_only_ceiling_book_and_whitelist(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "decisions.jsonl"
            st = pe.State()
            p.write_text("")
            self.assertEqual(pe.tail_signals(p, st, pe.DEFAULT_BOOK), [])  # first run: start at end
            p.write_text(
                enter_row(action="skip") + enter_row(action="exit", pnl_lamports=5, exit_reason="tp")
                + enter_row(ledger="shadow") + enter_row(book="other") + "garbage with enter\n"
                + enter_row(mint="MINTOK", t=T0 + 1, pnl_lamports=999))
            out = pe.tail_signals(p, st, pe.DEFAULT_BOOK)
            self.assertEqual(len(out), 1)
            self.assertEqual(set(out[0]), set(pe.KEEP_FIELDS))
            self.assertEqual(out[0]["mint"], "MINTOK")
            self.assertNotIn("pnl_lamports", json.dumps(out))

    def test_offset_persists_and_partial_line_waits(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "decisions.jsonl"
            sp = Path(d) / "st.json"
            p.write_text("")
            st = pe.State()
            pe.tail_signals(p, st, pe.DEFAULT_BOOK)
            row = enter_row(mint="A")
            with p.open("a") as fh:
                fh.write(row + row[:20])  # one full line, one partial
            self.assertEqual(len(pe.tail_signals(p, st, pe.DEFAULT_BOOK)), 1)
            st.save(sp)
            st2 = pe.State.load(sp)  # restart: no replay
            self.assertEqual(pe.tail_signals(p, st2, pe.DEFAULT_BOOK), [])
            with p.open("a") as fh:
                fh.write(row[20:] + enter_row(mint="B"))
            got = pe.tail_signals(p, st2, pe.DEFAULT_BOOK)
            self.assertEqual([s["mint"] for s in got], ["A", "B"])

    def test_truncation_or_rotation_restarts(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "decisions.jsonl"
            p.write_text(enter_row(mint="A") * 3)
            st = pe.State()
            pe.tail_signals(p, st, pe.DEFAULT_BOOK)
            p.unlink()
            p.write_text(enter_row(mint="Z"))
            self.assertEqual([s["mint"] for s in pe.tail_signals(p, st, pe.DEFAULT_BOOK)], ["Z"])

    def test_seal_source_never_names_other_runner_files(self):
        src = Path(pe.__file__).read_text()
        for name in ("positions.jsonl", "runner-status", "latency.jsonl", "summary.json"):
            self.assertNotIn(name, src)
        self.assertEqual(pe.DECISIONS_FILE, "decisions.jsonl")
        for pat in (r"\.sign\(", r"Keypair", r"from_seed", r"sendTransaction", r"send_transaction"):
            self.assertIsNone(re.search(pat, src), pat)

    def test_executor_ignores_non_enter_rows(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d))
            dec = Path(conf["signals_dir"]) / "decisions.jsonl"
            dec.write_text("")
            ex.step()
            dec.write_text(enter_row(action="exit", pnl_lamports=1) + enter_row(action="skip", reason="x"))
            self.assertEqual(ex.step(), 0)
            self.assertEqual(fills(conf), [])
            self.assertEqual(ex.rpc.calls, [])


class PricingTests(unittest.TestCase):
    def test_v_priced_buy_uses_vault_plus_v(self):
        with tempfile.TemporaryDirectory() as d:
            rpc = FakeRpc()
            ex, conf = make(Path(d), rpc=rpc)
            ex.handle_signal(sig(ex, t=T0 - 500))
            buy = fills(conf)[0]
            q = rpc.quote + V
            fee = pe.fee_ppm_for(q, rpc.base)
            self.assertEqual(buy["expected_tokens"], tx.cp_buy_out(50_000_000, q, rpc.base, fee))
            self.assertLess(buy["expected_tokens"], tx.cp_buy_out(50_000_000, rpc.quote, rpc.base, fee))  # vault-only would overstate
            self.assertEqual(buy["v_lamports"], V)
            self.assertEqual(buy["pool_slot"], 77)
            self.assertEqual(buy["slippage_bps"], 1500)
            self.assertEqual(buy["priority_lamports"], 500_000)
            self.assertEqual(buy["cu_used"], 90_000)
            self.assertEqual(buy["ms_decision_to_built"], 500)
            self.assertEqual(ex.state.attempts, 1)
            self.assertIn(MINT, ex.state.open)

    def test_null_v_skips_without_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            rpc = FakeRpc(pool_b64=no_v_pool())
            ex, conf = make(Path(d), rpc=rpc)
            ex.handle_signal(sig(ex))
            row = fills(conf)[0]
            self.assertEqual((row["kind"], row["reason"]), ("skip", "no_v"))
            self.assertEqual(ex.state.attempts, 0)
            self.assertNotIn("simulateTransaction", rpc.calls)

    def test_no_pool_and_stale(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d), rpc=FakeRpc(owner="11111111111111111111111111111111"))
            ex.handle_signal(sig(ex))
            ex.handle_signal(sig(ex, t=T0 - 10**7))
            self.assertEqual([r["reason"] for r in fills(conf)], ["no_pool", "stale_signal"])

    def test_sim_error_recorded_no_position(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d), rpc=FakeRpc(sim_err={"InstructionError": [4, {"Custom": 1}]}))
            ex.handle_signal(sig(ex))
            row = fills(conf)[0]
            self.assertIsNotNone(row["err"])
            self.assertTrue(row["log_tail"])
            self.assertEqual(ex.state.open, {})
            self.assertEqual(ex.state.attempts, 1)


class ExitRuleTests(unittest.TestCase):
    def setUp(self):
        q = pe.entry_quote(self.snap(Q0), 50_000_000)
        self.pos = {"t_entry_ms": T0, "tokens": q["tokens"], "net_in": q["net_in"], "mark": q["mark"], "spend": 50_000_000}

    def snap(self, quote_vault):
        return pe.Snapshot(None, 1, quote_vault, BASE0, V)  # type: ignore[arg-type]

    def test_rule_is_tp50_sl30_30m(self):
        self.assertEqual((pe.EXIT_RULE.tp, pe.EXIT_RULE.sl, pe.MAX_HOLD_MS), (0.5, 0.3, 1_800_000))

    def test_hold(self):
        self.assertIsNone(pe.exit_check(self.pos, self.snap(Q0), T0 + 1000)["reason"])

    def test_tp(self):
        r = pe.exit_check(self.pos, self.snap(int((Q0 + V) * 1.6) - V), T0 + 1000)
        self.assertEqual(r["reason"], "tp")
        self.assertGreaterEqual(r["ret"], 0.5)

    def test_sl(self):
        r = pe.exit_check(self.pos, self.snap(int((Q0 + V) * 0.6) - V), T0 + 1000)
        self.assertEqual(r["reason"], "sl")
        self.assertLessEqual(r["ret"], -0.3)

    def test_time_stop(self):
        self.assertIsNone(pe.exit_check(self.pos, self.snap(Q0), T0 + 1_800_000)["reason"])
        self.assertEqual(pe.exit_check(self.pos, self.snap(Q0), T0 + 1_800_001)["reason"], "time_stop")

    def test_end_to_end_sl_closes_and_counts_loss(self):
        with tempfile.TemporaryDirectory() as d:
            rpc = FakeRpc()
            clock = Clock()
            ex, conf = make(Path(d), rpc=rpc, clock=clock)
            ex.handle_signal(sig(ex))
            rpc.quote = int((Q0 + V) * 0.5) - V
            clock.t += 5000
            ex.poll_positions()
            sell = fills(conf)[-1]
            self.assertEqual((sell["kind"], sell["exit_reason"]), ("sell", "sl"))
            self.assertLess(sell["pnl_lamports"], 0)
            self.assertEqual(ex.state.realized_lamports, sell["pnl_lamports"])
            self.assertEqual(ex.state.open, {})
            self.assertIsNone(sell["sell_probe_err"])


class FillSchemaTests(unittest.TestCase):
    def test_rows_are_dryrun_with_schema(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d))
            ex.handle_signal(sig(ex))
            row = fills(conf)[0]
            self.assertEqual((row["schema"], row["mode"], row["kind"]), ("probe_fill_v1", "dryrun", "buy"))
            for k in ("ts_ms", "mint", "book", "decision_t_ms", "pool", "pool_slot", "t_built_ms", "t_sim_ms", "expected_tokens",
                      "sim_tokens", "cu_used", "err", "log_tail", "spend_lamports", "v_lamports"):
                self.assertIn(k, row)

    def test_fill_log_is_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d))
            for _ in range(2):
                ex.handle_signal(sig(ex, mint=str(tx.WSOL_MINT)))
            self.assertEqual(len(fills(conf)), 2)

    def test_config_pins_defaults(self):
        cfg = json.loads((Path(__file__).parent.parent / "scripts/mal-fast/probe-executor.json").read_text())
        self.assertEqual(cfg["mode"], "dryrun")
        lim = pe.Limits.from_config(cfg)
        self.assertEqual((lim.size_lamports, lim.priority_lamports), (50_000_000, 500_000))
        self.assertEqual(cfg["book"], pe.DEFAULT_BOOK)
        self.assertNotIn("helius", json.dumps(cfg).lower())


if __name__ == "__main__":
    unittest.main()
