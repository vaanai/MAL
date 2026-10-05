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
        if method == "getAccountInfo" and params[0] == str(tx.GLOBAL_CONFIG):
            return {"context": {"slot": 77}, "value": {"owner": "x", "data": [GC["data_b64"], "base64"], "lamports": 1}}
        if method == "getAccountInfo":
            return {"context": {"slot": 77}, "value": {"owner": self.owner, "data": [self.pool_b64, "base64"], "lamports": 1}}
        if method == "getMultipleAccounts":
            self.multi_keys = list(params[0])
            vals = [{"owner": getattr(self, "mint_owner", str(tx.TOKEN_2022_PROGRAM)), "data": ["", "base64"], "lamports": 1},
                    tok_acct(self.base), tok_acct(self.quote)]
            if str(tx.GLOBAL_CONFIG) in params[0]:  # like the chain: one value per key requested
                vals.insert(0, {"owner": "x", "data": [GC["data_b64"], "base64"], "lamports": 1})
            return {"context": {"slot": 77}, "value": vals}
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
    conf = {"signals_dir": str(tmp / "sig"), "state_dir": str(tmp), "fill_log": str(tmp / "fills.jsonl"),
            "stop_file": str(tmp / "STOP"), "halt_file": str(tmp / "HALT"), **cfg}
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
        self.assertEqual(pe.check_sell(True), "halt_file")
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
            ex, conf = make(tmp, max_attempts=1, mode="live")
            ex.handle_signal(sig(ex))
            self.assertEqual(ex.state.attempts, 1)
            ex2, _ = make(tmp, max_attempts=1, mode="live")  # restart
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

    def test_halt_file_halts_sell(self):
        with tempfile.TemporaryDirectory() as d:
            rpc = FakeRpc()
            ex, conf = make(Path(d), rpc=rpc)
            ex.handle_signal(sig(ex))
            Path(conf["halt_file"]).write_text("")
            rpc.calls.clear()
            ex.poll_positions()
            self.assertEqual(rpc.calls, [])
            self.assertIn(MINT, ex.state.open)

    def test_loss_cap_stops_new_buys(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d), loss_cap_lamports=10, mode="live")
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
        self.assertEqual(pe.SIGNAL_FILES, ("decisions.jsonl", "intents.jsonl"))
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


class StopHaltDryRunTests(unittest.TestCase):
    def test_stop_halts_buys_only_halt_freezes_exits(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            clock = Clock()
            ex, conf = make(tmp, rpc=FakeRpc(), clock=clock)
            ex.handle_signal(sig(ex))
            self.assertIn(MINT, ex.state.open)
            (tmp / "STOP").write_text("")
            clock.t += pe.MAX_HOLD_MS + 1000
            (tmp / "HALT").write_text("")
            ex.poll_positions()
            self.assertIn(MINT, ex.state.open)  # HALT: frozen
            (tmp / "HALT").unlink()
            ex.poll_positions()
            self.assertNotIn(MINT, ex.state.open)  # STOP alone: exit proceeds
            ex.handle_signal(sig(ex, t=clock()))
            self.assertEqual(fills(conf)[-1]["reason"], "limit:stop_file")
            (tmp / "HALT").write_text("")
            self.assertEqual(pe.check_buy(ex.limits, ex.state, clock(), False, "live", True), "halt_file")


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


REPO = Path(__file__).parent.parent
UNIT = (REPO / "scripts/mal-fast/mal-probe-executor.service").read_text()


class RpcErrorProxy(Exception):
    def __str__(self):
        return "HTTP Error 429: too many requests"


class SealUnitTests(unittest.TestCase):
    def test_unit_whitelists_only_intents_jsonl(self):
        self.assertIn("TemporaryFileSystem=/var/lib/mal/paper/fast-forward-paper:ro", UNIT)
        binds = re.findall(r"^BindReadOnlyPaths=(.*)$", UNIT, re.M)
        self.assertEqual(len(binds), 1)
        self.assertEqual(binds[0].strip(), "-/var/lib/mal/paper/fast-forward-paper/intents.jsonl")
        self.assertNotRegex(UNIT, r"(?m)^ReadWritePaths=.*paper")
        self.assertNotRegex(UNIT, r"(?m)^ReadOnlyPaths=.*fast-forward-paper")

    def test_code_references_no_other_runner_filename(self):
        runner = (REPO / "tools/forward_paper.py").read_text()
        names = set(re.findall(r"""["']([A-Za-z0-9_.-]+\.jsonl?)["']""", runner)) - {"decisions.jsonl", "intents.jsonl"}
        self.assertIn("positions.jsonl", names)  # the scan finds the files it should
        src = Path(pe.__file__).read_text()
        for n in names:
            self.assertNotIn(n, src, n)
            self.assertNotIn(n, UNIT, n)

    def test_unit_hardening_present(self):
        for k in ("NoNewPrivileges=true", "ProtectSystem=strict", "ReadWritePaths=/var/lib/mal-live", "MemoryMax=1G", "User=mal-live",
                  "RestrictAddressFamilies=AF_INET AF_INET6", "CapabilityBoundingSet="):
            self.assertIn(k, UNIT)


def intent_row(**kw):
    row = {"schema": "forward_paper_intent_v1", "book": pe.DEFAULT_BOOK, "ledger": "ceiling", "mint": "M1", "creator": "C",
           "decision_t_ms": 1_000, "written_ms": 1_100, "trigger": "migrate", "score": 0.9}
    row.update(kw)
    return json.dumps(row) + "\n"


class IntentTests(unittest.TestCase):
    def test_parse_intent_whitelists_fields(self):
        got = pe.parse_intent(intent_row(pnl_lamports=5, creator="X"), pe.DEFAULT_BOOK, "ceiling")
        self.assertEqual(got, {"mint": "M1", "decision_t_ms": 1000, "score": 0.9, "trigger": "migrate",
                               "book": pe.DEFAULT_BOOK, "written_ms": 1100})

    def test_parse_intent_runner_kill_field(self):
        d = pe.DEFAULT_BOOK
        self.assertNotIn("runner_kill", pe.parse_intent(intent_row(), d, "ceiling"))  # old runner: unchanged
        self.assertNotIn("runner_kill", pe.parse_intent(intent_row(runner_kill=False), d, "ceiling"))
        self.assertIs(pe.parse_intent(intent_row(runner_kill=True), d, "ceiling")["runner_kill"], True)

    def test_runner_kill_refuses_buy_but_false_or_missing_buys(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d))
            s = sig(ex)
            s["runner_kill"] = True
            ex.handle_signal(s)
            self.assertEqual(fills(conf)[-1]["reason"], "runner_kill")
            self.assertEqual(ex.state.attempts, 0)
            self.assertEqual(ex.rpc.calls, [])
        for extra in ({}, {"runner_kill": False}):
            with tempfile.TemporaryDirectory() as d:
                ex, conf = make(Path(d))
                s = sig(ex)
                s.update(extra)
                ex.handle_signal(s)
                self.assertEqual(ex.state.attempts, 1)
                self.assertEqual(fills(conf)[-1]["kind"], "buy")

    def test_parse_intent_rejects_other_rows(self):
        self.assertIsNone(pe.parse_intent(intent_row(ledger="shadow"), pe.DEFAULT_BOOK, "ceiling"))
        self.assertIsNone(pe.parse_intent(intent_row(book="other"), pe.DEFAULT_BOOK, "ceiling"))
        self.assertIsNone(pe.parse_intent(intent_row(written_ms=None), pe.DEFAULT_BOOK, "ceiling"))
        self.assertIsNone(pe.parse_intent(intent_row(schema="forward_paper_decision_v1"), pe.DEFAULT_BOOK, "ceiling"))
        self.assertIsNone(pe.parse_intent(enter_row(), pe.DEFAULT_BOOK, "ceiling"))  # an `enter` row is not an intent
        self.assertIsNone(pe.parse_enter(intent_row(), pe.DEFAULT_BOOK, "ceiling"))

    def test_tail_intents_and_age_uses_written_ms(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "intents.jsonl"
            p.write_text("")
            st = pe.State()
            pe.tail_signals(p, st, pe.DEFAULT_BOOK, intents=True)
            p.write_text(intent_row())
            out = pe.tail_signals(p, st, pe.DEFAULT_BOOK, intents=True)
            self.assertEqual([s["mint"] for s in out], ["M1"])
            self.assertEqual(pe.signal_age_ms(out[0], 1_600), 500)  # written_ms, not the older tape time
            self.assertEqual(pe.signal_age_ms({"decision_t_ms": 1000}, 1_600), 600)

    def test_executor_reads_only_configured_signals_file(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d), signals_file="intents.jsonl")
            self.assertEqual(ex.decisions.name, "intents.jsonl")
            self.assertTrue(ex.use_intents)
            with self.assertRaises(ValueError):
                make(Path(d), signals_file="positions.jsonl")

    def test_shipped_configs_use_intents(self):
        for n in ("probe-executor.json", "probe-executor-live.json"):
            self.assertEqual(json.loads((REPO / "scripts/mal-fast" / n).read_text())["signals_file"], "intents.jsonl")


class MissingSignalsTests(unittest.TestCase):
    def _cfg(self, d: Path, **extra) -> dict:
        (d / "sig").mkdir(exist_ok=True)
        return {"signals_dir": str(d / "sig"), "signals_file": "intents.jsonl", "state_dir": str(d), "fill_log": str(d / "f.jsonl"), **extra}

    def test_live_startup_refuses_with_exit_2_when_file_missing(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d), mode="live")
            cfgp = Path(d) / "c.json"
            cfgp.write_text(json.dumps(cfg))
            out = io.StringIO()
            with redirect_stdout(out):
                rc = pe.main(["--config", str(cfgp), "--live"])
            self.assertEqual(rc, 2)
            self.assertIn("ALERT startup_refused", out.getvalue())
            self.assertIn("start the runner first", out.getvalue())

    def test_startup_check_passes_when_present_and_dryrun_only_warns(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(pe.startup_signals_check(cfg, live=False), 0)  # dry run: warn, no exit
                self.assertEqual(pe.startup_signals_check(cfg, live=True), 2)
            self.assertIn("WARNING", out.getvalue())
            (Path(d) / "sig" / "intents.jsonl").write_text("")
            self.assertEqual(pe.startup_signals_check(cfg, live=True), 0)

    def test_unreadable_file_refuses_live(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            f = Path(d) / "sig" / "intents.jsonl"
            f.write_text("")
            f.chmod(0)
            try:
                if os.access(f, os.R_OK):
                    self.skipTest("running as a user that ignores file modes")
                self.assertEqual(pe.startup_signals_check(cfg, live=True), 2)
            finally:
                f.chmod(0o600)

    def test_absent_alert_at_most_once_a_minute_and_clears(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            clock = Clock()
            ex, conf = make(Path(d), clock=clock, signals_file="intents.jsonl")
            out = io.StringIO()
            with redirect_stdout(out):
                ex.signal_tick()
                clock.t += 30_000
                ex.signal_tick()
                self.assertEqual(out.getvalue().count("signals_file_missing"), 1)
                clock.t += 31_000
                ex.signal_tick()
            self.assertEqual(out.getvalue().count("signals_file_missing"), 2)
            self.assertIn("WARNING", out.getvalue())  # dry run
            self.assertNotIn("ALERT", out.getvalue())

    def test_live_mode_absent_is_an_alert(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            ex, _ = make(Path(d), mode="live", signals_file="intents.jsonl")
            out = io.StringIO()
            with redirect_stdout(out):
                ex.signal_tick()
            self.assertIn("ALERT signals_file_missing", out.getvalue())


class ClampTests(unittest.TestCase):
    def test_direct_construction_is_clamped(self):
        lim = pe.Limits(max_attempts=999, max_open=9, loss_cap_lamports=10**12, max_days=99, size_lamports=10**10, priority_lamports=10**9)
        for k, cap in pe.DEC019_MAX.items():
            self.assertEqual(getattr(lim, k), cap)

    def test_nan_inf_and_bad_types_rejected(self):
        for bad in (float("nan"), float("inf"), -1, 0, True, "5"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                pe.Limits(max_days=bad)
        cfg = json.loads('{"max_days": NaN}')  # valid for Python's json
        with self.assertRaises(ValueError):
            pe.Limits.from_config(cfg)

    def test_slippage_clamped_and_validated(self):
        with tempfile.TemporaryDirectory() as d:
            ex, _ = make(Path(d), slippage_cap=0.9)
            self.assertEqual(ex.slip_bps, 1500)
            ex, _ = make(Path(d), slippage_cap=0.05)
            self.assertEqual(ex.slip_bps, 500)
            with self.assertRaises(ValueError):
                make(Path(d), slippage_cap=float("nan"))


class ModeStateTests(unittest.TestCase):
    def test_state_files_are_per_mode(self):
        self.assertNotEqual(pe.state_path_for("/x", "dryrun"), pe.state_path_for("/x", "live"))

    def test_mode_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            pe.State(mode="dryrun").save(p)
            with self.assertRaises(SystemExit):
                pe.State.load(p, "live")

    def test_live_refuses_missing_state_with_live_fill_rows(self):
        with tempfile.TemporaryDirectory() as d:
            fl = Path(d) / "f.jsonl"
            fl.write_text(json.dumps({"mode": "dryrun"}, separators=(",", ":")) + "\n")
            pe.guard_live_state("live", Path(d) / "state-live.json", fl)  # dry rows only: ok
            fl.write_text(json.dumps({"mode": "live"}, separators=(",", ":")) + "\n")
            with self.assertRaises(SystemExit):
                pe.guard_live_state("live", Path(d) / "state-live.json", fl)
            pe.guard_live_state("dryrun", Path(d) / "state-dryrun.json", fl)  # dry run unaffected

    def test_dryrun_state_not_shared(self):
        with tempfile.TemporaryDirectory() as d:
            ex, _ = make(Path(d))
            ex.handle_signal(sig(ex))
            self.assertTrue((Path(d) / "state-dryrun.json").exists())
            self.assertFalse((Path(d) / "state-live.json").exists())

    def test_attempt_is_written_ahead(self):
        with tempfile.TemporaryDirectory() as d:
            class Boom(FakeRpc):
                def __call__(self, method, params):
                    if method == "simulateTransaction":
                        raise KeyboardInterrupt  # crash mid-simulation
                    return super().__call__(method, params)
            ex, _ = make(Path(d), rpc=Boom())
            with self.assertRaises(KeyboardInterrupt):
                ex.handle_signal(sig(ex))
            self.assertEqual(pe.State.load(Path(d) / "state-dryrun.json").attempts, 1)


class UnpricedCloseTests(unittest.TestCase):
    def _open(self, d):
        rpc = FakeRpc()
        clock = Clock()
        ex, conf = make(Path(d), rpc=rpc, clock=clock)
        ex.handle_signal(sig(ex))
        return ex, conf, clock, ex.state.open[MINT]["t_entry_ms"]

    @staticmethod
    def _fail(m, p):
        raise RpcErrorProxy()

    def test_rpc_failure_past_deadline_retries_within_grace(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf, clock, entry = self._open(d)
            ex.rpc = self._fail
            clock.t = entry + pe.MAX_HOLD_MS + 1000
            ex.poll_positions()
            self.assertIn(MINT, ex.state.open)  # still open: retry, no loss booked
            self.assertEqual(ex.state.realized_lamports, 0)

    def test_forced_close_is_labelled_and_not_booked(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf, clock, entry = self._open(d)
            ex.rpc = self._fail
            clock.t = entry + pe.MAX_HOLD_MS + pe.UNPRICED_GRACE_MS + 1
            ex.poll_positions()
            sell = fills(conf)[-1]
            self.assertEqual(sell["exit_reason"], "timeout_unpriced")
            self.assertIsNone(sell["pnl_lamports"])
            self.assertEqual(ex.state.realized_lamports, 0)
            self.assertEqual(ex.state.open, {})


class RpcLimiterTests(unittest.TestCase):
    def test_label_classification(self):
        self.assertEqual(pe.error_label(RpcErrorProxy()), "rate_limited")
        self.assertEqual(pe.error_label(SystemExit("rpc x failed: TimeoutError: timed out")), "timeout")
        self.assertEqual(pe.error_label(SystemExit("rpc x failed: HTTPError: HTTP Error 503")), "server_error")
        self.assertEqual(pe.error_label(pe.RpcError("rate_limited")), "rate_limited")

    def test_token_bucket_caps_to_2rps(self):
        t = [0.0]

        def sleep(x):
            t[0] += x
        lim = pe.LimitedRpc(lambda m, p: {}, rps=100, clock=lambda: t[0], sleep=sleep)  # asked 100, capped to 2
        for _ in range(11):
            lim("m", [])
        self.assertGreaterEqual(t[0], 5.0 - 1e-9)  # 11 calls at 2 rps take >= 5 s

    def test_backoff_then_success_and_final_label(self):
        t = [0.0]

        def sleep(x):
            t[0] += x
        n = {"i": 0}

        def flaky(m, p):
            n["i"] += 1
            if n["i"] < 3:
                raise RpcErrorProxy()
            return {"ok": 1}
        lim = pe.LimitedRpc(flaky, clock=lambda: t[0], sleep=sleep)
        self.assertEqual(lim("m", []), {"ok": 1})
        self.assertGreaterEqual(t[0], 3.0)  # 1 s then 2 s backoff

        def always(m, p):
            raise RpcErrorProxy()
        lim2 = pe.LimitedRpc(always, retries=1, clock=lambda: t[0], sleep=sleep)
        with self.assertRaises(pe.RpcError) as cm:
            lim2("m", [])
        self.assertEqual(cm.exception.label, "rate_limited")

    def test_url_never_in_errors_or_log(self):
        def leaky(m, p):
            raise SystemExit("rpc x failed: https://h/?api-key=SECRET123 HTTP Error 429")
        lim = pe.LimitedRpc(leaky, retries=0, clock=lambda: 0.0, sleep=lambda x: None)
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d), rpc=lim)
            ex.handle_signal(sig(ex))
            blob = Path(conf["fill_log"]).read_text()
            self.assertNotIn("SECRET123", blob)
            self.assertNotIn("api-key", blob)
            self.assertEqual(json.loads(blob.splitlines()[0])["reason"], "rate_limited")

    def test_tail_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "decisions.jsonl"
            p.write_text("")
            st = pe.State()
            pe.tail_signals(p, st, pe.DEFAULT_BOOK)
            row = enter_row()
            total = pe.MAX_READ_BYTES // len(row) + 50
            p.write_text(row * total)
            first = pe.tail_signals(p, st, pe.DEFAULT_BOOK)
            self.assertLess(st.offset, p.stat().st_size)  # one pass did not consume everything
            self.assertLessEqual(st.offset, pe.MAX_READ_BYTES)
            second = pe.tail_signals(p, st, pe.DEFAULT_BOOK)
            self.assertEqual(len(first) + len(second), total)


class DryrunSoftStopTests(unittest.TestCase):
    def test_live_halts_dryrun_does_not(self):
        lim = pe.Limits()
        over = pe.State(attempts=30, realized_lamports=-250_000_000, first_attempt_ms=T0)
        late = T0 + 4 * 86_400_000
        self.assertEqual(pe.check_buy(lim, pe.State(attempts=30), T0, False, "live"), "max_attempts")
        self.assertEqual(pe.check_buy(lim, pe.State(realized_lamports=-250_000_000), T0, False, "live"), "loss_cap")
        self.assertEqual(pe.check_buy(lim, pe.State(first_attempt_ms=T0), late, False, "live"), "max_days")
        self.assertIsNone(pe.check_buy(lim, over, late, False, "dryrun"))
        # the hard stops still apply in dry run
        self.assertEqual(pe.check_buy(lim, pe.State(open={"a": {}, "b": {}, "c": {}}), T0, False, "dryrun"), "max_open")
        self.assertEqual(pe.check_buy(lim, over, late, True, "dryrun"), "stop_file")

    def test_dryrun_continues_past_budget_and_logs_would_halt(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf = make(Path(d), max_attempts=1)
            for i in range(3):
                ex.state.open.clear()
                ex.handle_signal(sig(ex))
            buys = [r for r in fills(conf) if r["kind"] == "buy"]
            self.assertEqual(len(buys), 3)
            self.assertEqual([r["would_have_halted"] for r in buys], [[], ["max_attempts"], ["max_attempts"]])
            self.assertEqual(ex.state.would_halt, {"max_attempts": 2})
            self.assertEqual(pe.State.load(Path(d) / "state-dryrun.json").would_halt, {"max_attempts": 2})



class TickClock:
    """Advances 5 ms per read, so every stage stamp is distinct and ordered."""

    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        self.t += 5
        return self.t


class SignalTickTests(unittest.TestCase):
    def _ex(self, d, **cfg):
        clock = Clock()
        rpc = FakeRpc()
        ex, conf = make(Path(d), rpc=rpc, clock=clock, **cfg)
        dec = Path(conf["signals_dir"]) / "decisions.jsonl"
        dec.write_text("")
        ex.tick()  # starts at end of file; slow work (pre-warm) runs once
        return ex, conf, clock, rpc, dec

    def test_clamps(self):
        self.assertEqual(pe.clamp_signal_poll_ms(None), 50)
        self.assertEqual(pe.clamp_signal_poll_ms(1), 20)
        self.assertEqual(pe.clamp_signal_poll_ms(10_000), 500)
        self.assertEqual(pe.clamp_signal_poll_ms(120), 120)
        for bad in (float("nan"), float("inf"), "50", True, [1]):
            self.assertEqual(pe.clamp_signal_poll_ms(bad), 50)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(make(Path(d), signal_poll_ms=0)[0].signal_poll_ms, 20)
            self.assertEqual(make(Path(d), signal_poll_ms=99999)[0].signal_poll_ms, 500)
            self.assertEqual(make(Path(d))[0].signal_poll_ms, 50)

    def test_fast_tail_handles_row_while_position_poll_pending(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf, clock, rpc, dec = self._ex(d)
            ex.state.open["OPENMINT"] = {"mint": "OPENMINT", "pool": "p", "t_entry_ms": clock(), "tokens": 1, "net_in": 1, "mark": 1.0, "spend": 1}
            polls = []
            ex.poll_positions = lambda: polls.append(1)
            clock.t += 100  # slow timer not due (poll_ms 5000)
            with dec.open("a") as fh:
                fh.write(enter_row(t=clock.t))
            self.assertEqual(ex.tick(), 1)  # one signal tick, buy done in it
            self.assertEqual(polls, [])
            self.assertEqual([r["kind"] for r in fills(conf)], ["buy"])

    def test_stat_first_no_read_when_unchanged(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            ex, conf, clock, rpc, dec = self._ex(d)
            with mock.patch.object(pe, "tail_signals", wraps=pe.tail_signals) as spy:
                for _ in range(5):
                    self.assertEqual(ex.signal_tick(), 0)
                self.assertEqual(spy.call_count, 0)
                with dec.open("a") as fh:
                    fh.write(enter_row(t=clock.t))
                ex.signal_tick()
                self.assertEqual(spy.call_count, 1)
                ex.signal_tick()
                self.assertEqual(spy.call_count, 1)

    def test_buy_defers_slow_work_one_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf, clock, rpc, dec = self._ex(d)
            polls = []
            ex.poll_positions = lambda: polls.append(clock.t)
            clock.t += 5_100  # slow work due
            with dec.open("a") as fh:
                fh.write(enter_row(t=clock.t))
            ex.tick()
            self.assertEqual(polls, [])  # buy first, positions wait
            clock.t += 50
            ex.tick()
            self.assertEqual(len(polls), 1)
            # never starved: overdue by more than a full period runs even with a buy
            clock.t += 10_500
            with dec.open("a") as fh:
                fh.write(enter_row(mint="OTHER", t=clock.t))
            ex.tick()
            self.assertEqual(len(polls), 2)

    def test_stage_fields_present_and_monotonic(self):
        with tempfile.TemporaryDirectory() as d:
            clock = TickClock()
            ex, conf = make(Path(d), rpc=FakeRpc(), clock=clock)
            dec = Path(conf["signals_dir"]) / "decisions.jsonl"
            dec.write_text("")
            ex.tick()
            lat = {"recv_to_decision_ms": 3, "applied_latency_ms": 250}
            with dec.open("a") as fh:
                fh.write(enter_row(t=clock.t, latency=lat))
            ex.tick()
            row = [r for r in fills(conf) if r["kind"] == "buy"][0]
            for k in ("decision_t_ms", "seen_ms", "state_ms", "state_slot", "built_ms", "simulated_ms", "latency"):
                self.assertIn(k, row)
            self.assertEqual(row["latency"], lat)
            self.assertEqual(row["state_slot"], 77)
            self.assertLessEqual(row["decision_t_ms"], row["seen_ms"])
            self.assertLess(row["seen_ms"], row["state_ms"])
            self.assertLess(row["state_ms"], row["built_ms"])
            self.assertLess(row["built_ms"], row["simulated_ms"])

    def test_latency_copied_only_when_present_and_small(self):
        row = json.loads(enter_row())
        self.assertNotIn("latency", pe.parse_enter(json.dumps(row), pe.DEFAULT_BOOK, "ceiling"))
        row["latency"] = {"x": "y" * 5000}
        self.assertNotIn("latency", pe.parse_enter(json.dumps(row), pe.DEFAULT_BOOK, "ceiling"))
        row["latency"] = {"applied_latency_ms": 1}
        self.assertEqual(pe.parse_enter(json.dumps(row), pe.DEFAULT_BOOK, "ceiling")["latency"], {"applied_latency_ms": 1})

    def test_global_config_cached_then_refetched_after_ttl(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf, clock, rpc, dec = self._ex(d)  # tick() pre-warmed the static cache
            self.assertIsNotNone(ex.static.gc)
            ex._snapshot(MINT)
            self.assertNotIn(str(tx.GLOBAL_CONFIG), rpc.multi_keys)
            self.assertEqual(len(rpc.multi_keys), 3)
            clock.t += pe.STATIC_TTL_MS + 1
            ex._snapshot(MINT)
            self.assertIn(str(tx.GLOBAL_CONFIG), rpc.multi_keys)

    def test_prewarm_refresh_failure_keeps_cache(self):
        with tempfile.TemporaryDirectory() as d:
            ex, conf, clock, rpc, dec = self._ex(d)
            gc = ex.static.gc

            def boom(m, p):
                raise RpcErrorProxy()
            ex.rpc = boom
            clock.t += pe.STATIC_REFRESH_MS + 1
            ex.prewarm()
            self.assertIs(ex.static.gc, gc)


class LimiterPriorityTests(unittest.TestCase):
    def _lim(self, hook=None):
        t = [0.0]

        def sleep(x):
            t[0] += x
        lim = pe.LimitedRpc(lambda m, p: {}, rps=2, clock=lambda: t[0], sleep=sleep)
        lim.idle_hook = hook
        return lim, t

    def test_priority_burst_skips_wait_but_rate_stays_capped(self):
        lim, t = self._lim()
        with lim.priority():
            for _ in range(4):
                lim("m", [])
        self.assertEqual(t[0], 0.0)  # buy path: a burst of 4 goes straight out
        lim2, t2 = self._lim()
        with lim2.priority():
            for _ in range(21):
                lim2("m", [])
        self.assertGreaterEqual(t2[0], (21 - pe.PRIORITY_BURST) * 0.5 - 1e-9)  # sustained rate still capped at 2 rps

    def test_non_priority_waits_behind_schedule(self):
        lim, t = self._lim()
        lim("m", [])
        lim("m", [])
        self.assertAlmostEqual(t[0], 0.5)

    def test_idle_hook_runs_during_non_priority_wait_only(self):
        hits = []
        lim, t = self._lim(hook=lambda: hits.append(1))
        lim("m", [])
        lim("m", [])  # waits 0.5 s in 20 ms slices
        self.assertGreaterEqual(len(hits), 20)
        n = len(hits)
        with lim.priority():
            lim("m", [])
            lim("m", [])
        self.assertEqual(len(hits), n)

    def test_hook_is_not_reentrant(self):
        depth = []
        lim, t = self._lim()

        def hook():
            depth.append(lim._in_hook)
            lim("m", [])  # a call made from inside the hook must not recurse into the hook
        lim.idle_hook = hook
        lim("m", [])
        lim("m", [])
        self.assertTrue(depth and all(depth))

    def test_new_signal_handled_during_a_position_poll_wait(self):
        with tempfile.TemporaryDirectory() as d:
            t = [0.0]
            fired = []
            dec_holder = []

            def sleep(x):
                t[0] += x
                if not fired and dec_holder:  # a signal lands while a position poll waits on the limiter
                    fired.append(1)
                    with dec_holder[0].open("a") as fh:
                        fh.write(enter_row(t=T0))
            lim = pe.LimitedRpc(FakeRpc(), rps=2, clock=lambda: t[0], sleep=sleep)
            ex, conf = make(Path(d), rpc=lim, clock=Clock())
            dec = Path(conf["signals_dir"]) / "decisions.jsonl"
            dec.write_text("")
            ex.signal_tick()
            dec_holder.append(dec)
            lim.idle_hook = ex.signal_tick
            gc = [str(tx.GLOBAL_CONFIG), {"encoding": "base64"}]
            lim("getAccountInfo", gc)
            lim("getAccountInfo", gc)  # non-priority: waits, slices, the hook runs the buy
            self.assertEqual([r["kind"] for r in fills(conf)], ["buy"])


class LatencyReportTests(unittest.TestCase):
    def test_report_p50_p90_and_no_pnl_or_key(self):
        with tempfile.TemporaryDirectory() as d:
            fl = Path(d) / "fills.jsonl"
            rows = []
            for i in range(10):
                rows.append({"mode": "dryrun", "kind": "buy", "decision_t_ms": 1000, "seen_ms": 1010 + i, "state_ms": 1100 + i,
                             "built_ms": 1110 + i, "simulated_ms": 1300 + i, "latency": {"applied_latency_ms": 250}, "pnl_lamports": 5})
            rows.append({"mode": "dryrun", "kind": "skip", "seen_ms": 1})
            rows.append({"mode": "live", "kind": "buy", "decision_t_ms": 1000, "seen_ms": 1020, "state_ms": 1120, "built_ms": 1130,
                         "sent_ms": 1140, "state_slot": 100, "landed_slot": 103})
            fl.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
            out = pe.latency_report({"fill_log": str(fl)})
            self.assertIn("[dryrun] buy rows with stage stamps: 10", out)
            self.assertRegex(out, r"decision_to_seen\s+n=10\s+p50=14\.5 p90=18\.1")
            self.assertRegex(out, r"decision_to_simulated\s+n=10\s+p50=304\.5")
            self.assertIn("[live] buy rows with stage stamps: 1", out)
            self.assertRegex(out, r"decision_to_sent\s+n=1\s+p50=140\.0")
            self.assertRegex(out, r"state_to_landed_slots\s+n=1\s+p50=3\.0")
            self.assertRegex(out, r"runner_applied_latency_ms\s+n=10\s+p50=250\.0")
            self.assertNotIn("pnl", out)

    def test_cli_and_empty(self):
        with tempfile.TemporaryDirectory() as d:
            cfgp = Path(d) / "c.json"
            cfgp.write_text(json.dumps({"fill_log": str(Path(d) / "none.jsonl")}))
            self.assertEqual(pe.main(["--config", str(cfgp), "--latency-report"]), 0)
            self.assertEqual(pe.latency_report({"fill_log": str(Path(d) / "none.jsonl")}), "no buy rows with stage stamps")

    def test_configs_carry_signal_poll(self):
        for name in ("probe-executor.json", "probe-executor-live.json"):
            cfg = json.loads((REPO / "scripts/mal-fast" / name).read_text())
            self.assertEqual(cfg["signal_poll_ms"], 50)


class LatencyWhitelistTests(unittest.TestCase):
    def test_only_finite_numeric_values_are_kept(self):
        row = json.loads(enter_row())
        row["latency"] = {"applied_latency_ms": 250, "note": "pnl=5", "flag": True, "bad": None, "nested": {"a": 1}}
        got = pe.parse_enter(json.dumps(row), pe.DEFAULT_BOOK, "ceiling")
        self.assertEqual(got["latency"], {"applied_latency_ms": 250})
        row["latency"] = {"note": "text only"}
        self.assertNotIn("latency", pe.parse_enter(json.dumps(row), pe.DEFAULT_BOOK, "ceiling"))


if __name__ == "__main__":
    unittest.main()
