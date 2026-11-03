"""h5-lane-syn (QP ruling 1103): shadow wrapper tools/h5_lane_syn_shadow.py and executor tools/h5_lane_syn_executor.py. Synthetic fixtures only: nothing here
reads /data/mal, /var/lib/mal, a key, a ledger or a real row. The executor fixtures are those of tools/test_h5_lane_executor.py / test_h5_executor_shadow.py."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools import h5_lane_executor as L
from tools import h5_lane_shadow as ls
from tools import h5_lane_syn_executor as X
from tools import h5_lane_syn_shadow as S
from tools import h5_shadow as hs
from tools.test_h5_executor import Case, Env, buy_args
from tools.test_h5_executor_shadow import shadow_trigger
from tools.test_h5_shadow import SOL, SPS, Tape, announce, boost_buys, drain, types

CUT_S = 1_793_923_200  # 2026-11-06T00:00:00Z
ROOT = Path(__file__).resolve().parent.parent
# blob ids at main ffe14359 (git hash-object); the h5-lane-syn PR must not move any of them
PINNED_BLOBS = {"tools/h5_shadow.py": "f23cf492211fd79287fa6ac82fd382f1f3e39a3d", "tools/h5_lane_shadow.py": "ea262dfd7718fa6a24665b8d2574624f63686fc2",
                "tools/h5_lane_executor.py": "c04b8e31362142cfe669b28b0506162d4bea990a", "tools/h5_executor.py": "03995837317f65f62db2fb6b238ecaca956d01cb"}
OBSERVE_TREE = "38db2662b78aa5d6f192073926b452230781773a"


def git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class PinnedFilesTests(unittest.TestCase):
    def test_pinned_files_are_byte_identical_to_main(self):
        for rel, want in PINNED_BLOBS.items():
            self.assertEqual(git_blob(ROOT / rel), want, rel)

    def test_observe_tree_is_untouched(self):
        r = subprocess.run(["git", "rev-parse", "HEAD:observe"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            self.assertEqual(r.stdout.strip(), OBSERVE_TREE)


# ---------------------------------------------------------------- shadow ----------------------------------------------------------------------------------
def mk_tape(ts0: int) -> Tape:
    t = Tape(ts0=ts0)
    t.row(1000, "buy", "A", SOL // 10)
    boost_buys(t, 1000)
    drain(t, 1200, 35.0)
    t.row(1210, "buy", "X", SOL)
    t.row(1900, "buy", "W", SOL)
    return t


def mk_engine(cls, clf, **kw):
    out: list[dict] = []
    now = [1_800_000_000_000]
    eng = cls(out.append, wall=lambda: now[0], sps_fn=lambda p: SPS, pda_fn=lambda pool: "PDA_" + pool, classifier=clf, seal_start_ms=None, **kw)
    announce(eng)
    return eng, out


def run(cls, clf, ts0=CUT_S + 1000, **kw):
    eng, out = mk_engine(cls, clf, **kw)
    for r in mk_tape(ts0).rows:
        eng.on_trade(r)
    eng.close_all("t")
    return eng, out


class ShadowTests(unittest.TestCase):
    def test_synthetic_pool_after_the_cut_writes_tagged_triggers_and_no_outcome(self):
        for src in ("ws", "rpc"):
            eng, out = run(S.SynEngine, hs.StaticClassifier(True, src))
            trig = types(out, "trigger")
            self.assertEqual(sorted(r["variant"] for r in trig), ["fv", "pv"], src)
            for r in trig:
                self.assertIs(r["synthetic"], True)
                self.assertEqual((r["synthetic_src"], r["population"], r["s0_block_time"]), (src, "synthetic_only", CUT_S + 1000))
                self.assertTrue(r["h5_look2_sealed"])  # the Look-2 seal stays on: decision-time record only
                self.assertTrue(S.trigger_ok(r))
            for t in ("outcome", "outcome_bx10", "strip", "excluded"):
                self.assertEqual(types(out, t), [], t)

    def test_every_other_class_is_excluded_with_no_trigger_and_no_quote_fields(self):
        cases = [(False, "rpc", "plain"), (False, "ws", "plain"), (None, None, "unclassified"), (True, "pre_event_binary", "unclassified"),
                 (True, None, "unclassified"), (True, "test", "unclassified")]
        for syn, src, reason in cases:
            eng, out = run(S.SynEngine, hs.StaticClassifier(syn, src))
            self.assertEqual(types(out, "trigger"), [], (syn, src))
            ex = types(out, "excluded")
            self.assertEqual([r["reason"] for r in ex], [reason], (syn, src))
            self.assertEqual(set(ex[0]) - {"v", "schema", "t_ms"}, {"type", "reason", "pool", "mint", "s0", "slot"})
            self.assertEqual(eng.counters[f"excluded_{reason}"], 1)

    def test_s0_cut_before_missing_and_at_the_boundary(self):
        for ts0, want in ((CUT_S - 1, "syn_pre_window"), (CUT_S - 86_400, "syn_pre_window"), (0, "syn_pre_window"), (CUT_S, None), (CUT_S + 5, None)):
            eng, out = run(S.SynEngine, hs.StaticClassifier(True, "ws"), ts0=ts0)
            if want:
                self.assertEqual(types(out, "trigger"), [], ts0)
                self.assertEqual([r["reason"] for r in types(out, "excluded")], [want], ts0)
                self.assertEqual(eng.counters["excluded_syn_pre_window"], 1)
            else:
                self.assertEqual(len(types(out, "trigger")), 2, ts0)
        self.assertEqual(S.SYN_CUT_MS, 1793923200000)
        self.assertFalse(S.after_cut(None) or S.after_cut(True) or S.after_cut("x") or S.after_cut(-1))

    def test_no_trigger_in_the_output_ever_says_synthetic_false_or_null(self):
        eng, out = mk_engine(S.SynEngine, hs.StaticClassifier(False, "rpc"))
        for bad in ({"type": "trigger", "synthetic": False}, {"type": "trigger", "synthetic": None}):
            eng.emit(dict(bad))  # a trigger record outside the gate is dropped
        self.assertEqual(types(out, "trigger"), [])
        self.assertEqual(eng.counters["syn_trigger_dropped_no_gate"], 2)

    def test_decision_fields_equal_the_stock_fire(self):
        _, stock = run(hs.Engine, hs.StaticClassifier(False, "rpc"))
        _, syn = run(S.SynEngine, hs.StaticClassifier(True, "rpc"))
        added = {"synthetic", "population", "s0_block_time"}

        def proj(out):
            rows = [{k: v for k, v in r.items() if k not in added} for r in types(out, "trigger")]
            return hashlib.md5(json.dumps(rows, sort_keys=True).encode()).hexdigest(), len(rows)

        self.assertEqual(proj(stock), proj(syn))
        self.assertEqual(proj(syn)[1], 2)

    def test_stock_engine_still_refuses_a_synthetic_pool(self):
        _, out = run(hs.Engine, hs.StaticClassifier(True, "ws"))
        self.assertEqual(types(out, "trigger"), [])

    def test_the_look2_seal_cannot_be_turned_off(self):
        with self.assertRaises(ValueError):
            S.SynEngine(lambda r: None, h5_look2_start_ms=None)
        with self.assertRaises(ValueError):
            S.SynEngine(lambda r: None, h5_look2_observed=True)

    def test_start_record_tags(self):
        eng, out = mk_engine(S.SynEngine, hs.StaticClassifier(True, "ws"))
        eng.emit({"type": "start", "seal": {"reason": "x"}})
        (st,) = types(out, "start")
        self.assertEqual((st["seal"]["population"], st["seal"]["lane"], st["bot_id"], st["syn_cut_ms"]), ("synthetic_only", "DEC-035", "h5-lane-syn", S.SYN_CUT_MS))

    def test_main_refusals_and_install(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}):
                os.makedirs(f"{home}/data")
                good = f"{home}/data/h5-shadow-lane-syn"
                rpc = ["--rpc-url", "https://example-rpc.invalid"]
                base = ["--out-dir", good, *rpc]
                for argv in (["--h5-look2-observed", "EXP-024-Am2", *base], ["--fresh-forward", "ARENA", *base], ["--q-star-sol", "50", *base],
                             ["--out-dir", good], ["--out-dir", good, "--rpc-url", hs.DEFAULT_RPC], ["--out-dir", f"{home}/data/h5-shadow-lane1", *rpc],
                             ["--out-dir", f"{home}/data/h5-shadow", *rpc], [*rpc]):
                    self.assertEqual(S.main(argv), 2, argv)
                seen = {}

                def fake(argv):
                    seen["engine"], seen["argv"] = hs.Engine, argv
                    return 0

                orig_engine, orig_oracle = hs.Engine, hs.cap_pick_oracle_from_env
                with mock.patch.object(ls, "main", fake):
                    self.assertEqual(S.main(base), 0)
                self.assertIs(seen["engine"], S.SynEngine)
                self.assertEqual(seen["argv"][:2], ["--lane", "DEC-035"])
                self.assertIs(hs.Engine, orig_engine)
                self.assertIs(hs.cap_pick_oracle_from_env, orig_oracle)


# ---------------------------------------------------------------- executor --------------------------------------------------------------------------------
BOT = "h5-lane-syn"
STAKE = 20_000_000
RESERVE = L.LANE_MIN_FEE_RESERVE
S0_OK = CUT_S + 3600


class SynEnv(Env):
    def build(self):
        ex = X.H5LaneSynExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock, pick_oracle=self.oracle, root=self.root)
        self.seed_clock(ex)
        return ex


def start_rec(**over) -> dict:
    rec = {"type": "start", "t_ms": 1, "out_dir": "/home/x/data/h5-shadow-lane-syn", "seal": {"reason": "x", "population": "synthetic_only", "lane": "DEC-035",
           "oracle": "lane_dec035_not_a_pick"}, "bot_id": BOT, "syn_cut_ms": S.SYN_CUT_MS,
           "h5_look2": {"reason": "r", "start_ms": hs.H5_LOOK2_START_MS, "end_ms": hs.H5_LOOK2_END_MS, "observed": False, "amendment_ref": None},
           "wrapper_module": S.WRAPPER_MODULE, "wrapper_sha256": S.wrapper_sha256()}
    return {**rec, **over}


def syn_row(clock, **kw) -> dict:
    return shadow_trigger(clock, **{**dict(synthetic=True, synthetic_src="ws", population="synthetic_only", s0_block_time=S0_OK), **kw})


class SynCase(Case):
    def setUp(self):
        super().setUp()
        h.TIER_FILE_PATH.write_text("T1\n")
        os.chmod(h.TIER_FILE_PATH, 0o644)

    def lane_cfg(self, d: Path | None = None, **kw) -> dict:
        d = d or self.tmp / "lane"
        return {**dict(ledger_path=str(d / "ledger.jsonl"), stop_path=str(d / "STOP"), bot_id=BOT, stake_lamports=STAKE, max_open=3, budget_lamports=300_000_000,
                       daily_loss_lamports=100_000_000, fee_reserve_lamports=RESERVE, mark_stale_s=60), **kw}

    def syn_cfg(self, d: Path | None = None, **kw) -> dict:
        d = d or self.tmp / "lane"
        return {**dict(stop_path=str(d / "SYN-STOP"), pause_path=str(d / "SYN-PAUSE")), **kw}

    def syn_env(self, sub: str | None = None, lane_kw=None, syn_kw=None, **kw) -> SynEnv:
        tmp = self.tmp
        if sub:
            tmp = self.tmp / sub
            tmp.mkdir()
        d = tmp / "lane"
        e = SynEnv(tmp, lane=self.lane_cfg(d, **(lane_kw or {})), lane_syn=self.syn_cfg(d, **(syn_kw or {})), **kw)
        e.rpc.balance = 10 * 10**9
        return e

    @staticmethod
    def feed(e: Env, *rows: dict, start="default") -> int:
        p = Path(e.conf["intents_file"])
        if not p.exists():
            p.write_text("" if start is None else json.dumps(start_rec() if start == "default" else start) + "\n")
            e.ex.intent_tick()
        with p.open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return e.ex.intent_tick()

    @staticmethod
    def rows(e: Env, ev: str | None = None) -> list[dict]:
        p = e.ex.lane.path
        out = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in out if ev is None or r["ev"] == ev]

    @staticmethod
    def other(e: Env, bot: str = "other-bot") -> L.LaneLedger:
        return L.LaneLedger(L.LaneConfig(**{**e.ex.lane.cfg.__dict__, "bot_id": bot}))


class PopulationGateTests(SynCase):
    def test_a_synthetic_trigger_after_the_cut_is_bought_at_the_fixed_stake(self):
        for src in ("ws", "rpc"):
            e = self.syn_env(sub=src)
            self.assertEqual(self.feed(e, syn_row(e.clock, synthetic_src=src)), 1)
            self.assertEqual(buy_args(e.sent()[0])[0], STAKE)
            dec = e.ledger("decision")[0]
            self.assertEqual((dec["synthetic"], dec["synthetic_src"], dec["population"]), (True, src, "synthetic_only"))

    def test_s0_cut_is_rechecked_in_the_executor(self):
        for s0 in (CUT_S - 1, 0, None, "x", True):
            e = self.syn_env(sub=f"c{abs(hash(str(s0)))}")
            self.feed(e, syn_row(e.clock, s0_block_time=s0))
            self.assertEqual(e.rpc.sent, [], s0)
            self.assertEqual(e.ex.syn_refusals, {"syn_pre_window": 1}, s0)
        e = self.syn_env(sub="edge")
        self.feed(e, syn_row(e.clock, s0_block_time=CUT_S))
        self.assertEqual(len(e.rpc.sent), 1)

    def test_population_class_and_source_gates_fail_closed(self):
        bad = {"pop_missing": dict(population=None), "pop_plain": dict(population="plain"), "pop_old_value": dict(population="synthetic"), "false": dict(synthetic=False), "null": dict(synthetic=None),
               "str": dict(synthetic="true"), "src_none": dict(synthetic_src=None), "src_junk": dict(synthetic_src="pre_event_binary")}
        for name, kw in bad.items():
            e = self.syn_env(sub=name)
            self.feed(e, syn_row(e.clock, **kw))
            self.assertEqual(e.rpc.sent, [], name)
            self.assertEqual(sum(e.ex.syn_refusals.values()), 1, name)

    def test_a_plain_stock_trigger_row_is_not_traded(self):
        e = self.syn_env()
        self.feed(e, shadow_trigger(e.clock))  # synthetic false / rpc: the stock lane's row
        self.assertEqual(e.rpc.sent, [])

    def test_a_trigger_handed_in_without_the_gate_is_refused(self):
        e = self.syn_env()
        trg, bad = h.parse_shadow_trigger(shadow_trigger(e.clock))
        self.assertIsNone(bad)
        e.ex.handle_trigger(trg)
        self.assertEqual((e.refusals(), e.rpc.sent), (["syn_not_synthetic"], []))

    def test_the_parser_is_restored_after_the_tick(self):
        orig = h.parse_shadow_trigger
        e = self.syn_env()
        self.feed(e, syn_row(e.clock))
        self.assertIs(h.parse_shadow_trigger, orig)

    def test_config_refusals(self):
        e0 = self.tmp / "z"
        e0.mkdir()
        for lane_kw, syn_kw in (({"bot_id": "h5-lane-2"}, {}), ({"stake_lamports": 25_000_000}, {}), ({}, {"sub_budget_lamports": 60_000_001}),
                                ({}, {"surprise": 1}), ({}, {"pause_path": ""})):
            with self.assertRaises(SystemExit, msg=str((lane_kw, syn_kw))):
                self.syn_env(sub=f"r{abs(hash(str((lane_kw, syn_kw))))}", lane_kw=lane_kw, syn_kw=syn_kw)
        with self.assertRaises(SystemExit):
            SynEnv(self.tmp / "z", lane=self.lane_cfg(e0 / "lane"))  # no lane_syn block


class LimitTests(SynCase):
    def test_max_open_is_one_for_this_bot_not_the_whole_lane(self):
        e = self.syn_env()
        o = self.other(e)
        for i in range(2):  # two other-bot positions: the lane has room for a third, and this bot has none open
            self.assertIsNone(o.reserve(f"o{i}", f"s{i}", {}, e.clock(), STAKE))
        self.feed(e, syn_row(e.clock))
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(e.ex.h5.max_open, 1)
        e2 = self.syn_env(sub="b")
        e2.ex.lane.reserve("first", "sg1", {}, e2.clock(), STAKE)
        self.feed(e2, syn_row(e2.clock))
        self.assertEqual((e2.refusals(), e2.rpc.sent), (["syn_max_open"], []))

    def test_shared_lane_max_open_3_still_binds(self):
        e = self.syn_env()
        o = self.other(e)
        for i in range(3):
            self.assertIsNone(o.reserve(f"o{i}", f"s{i}", {}, e.clock(), STAKE))
        self.feed(e, syn_row(e.clock))
        self.assertEqual((e.refusals(), e.rpc.sent), (["lane_max_open"], []))

    def test_stake_is_fixed_at_the_ledger_too(self):
        e = self.syn_env()
        for stake in (19_999_999, 20_000_001, 25_000_000, 30_000_000):
            self.assertIn(e.ex.lane.reserve("m", "sg", {}, e.clock(), stake), ("syn_stake_not_fixed", "lane_stake_out_of_bounds"))
        self.assertIsNone(e.ex.lane.reserve("m", "sg", {}, e.clock(), STAKE))

    @staticmethod
    def lose(e: Env, lamports: int, mint: str = "seed-loss") -> None:
        e.ex.lane.record("fee", mint, {}, e.clock(), sol_out=lamports, fee_network=lamports)

    def test_sub_budget_presend_counts_open_stake_new_stake_and_reserve(self):
        # fresh bot: 0 - (20.0 + 4.11) = -24.11 (million lamports); allowed while loss + 24.11 < 60, i.e. loss < 35.89
        e = self.syn_env()
        self.lose(e, 35_000_000)
        self.assertIsNone(e.ex.lane.check({}, e.clock(), STAKE))
        e2 = self.syn_env(sub="p")
        self.lose(e2, 36_000_000)
        self.assertEqual(e2.ex.lane.reserve("m", "sg", {}, e2.clock(), STAKE), "syn_budget_presend")
        self.feed(e2, syn_row(e2.clock))
        self.assertEqual((e2.refusals(), e2.rpc.sent), (["syn_budget_presend"], []))

    def test_other_bots_losses_do_not_spend_this_bots_sub_budget(self):
        e = self.syn_env()
        o = self.other(e)
        o.record("fee", "oo", {}, e.clock(), sol_out=50_000_000, fee_network=50_000_000)
        self.assertIsNone(e.ex.lane.reserve("m", "sg", {}, e.clock(), STAKE))

    def test_sub_budget_stop_writes_its_own_file_and_never_resets(self):
        e = self.syn_env()
        stop, lane_stop = Path(e.ex.lane.syn["stop_path"]), Path(e.ex.lane.cfg.stop_path)
        self.lose(e, 60_000_000)
        self.assertTrue(stop.exists())
        self.assertFalse(lane_stop.exists())  # the lane-wide STOP is not written: only the per-bot file
        self.assertIn(BOT, stop.read_text())
        self.feed(e, syn_row(e.clock))
        self.assertEqual((e.refusals(), e.rpc.sent), (["syn_stop_file"], []))
        text = stop.read_text()
        e.ex.lane.write_syn_stop("again", e.clock())
        self.assertEqual(stop.read_text(), text)  # never rewritten
        e.jump(2 * 86_400_000)  # a new UTC day does not reset it
        self.feed(e, syn_row(e.clock))
        self.assertEqual(e.rpc.sent, [])
        e.ex.release_lane_lock()  # the first process is gone
        (self.tmp / "restart").mkdir()
        e2 = SynEnv(self.tmp / "restart", lane=self.lane_cfg(), lane_syn=self.syn_cfg())  # a restart on the same ledger and files
        e2.rpc.balance = 10 * 10**9
        self.assertTrue(e2.ex.lane.syn_stop_present())
        stop.unlink()  # removing the file does not reset it: the ledger re-derives the stop at the next check
        self.assertEqual(e2.ex.lane.reserve("m", "sg", {}, e2.clock(), STAKE), "syn_budget_stop")
        self.assertTrue(stop.exists())

    def test_sub_budget_can_only_be_lowered(self):
        e = self.syn_env(syn_kw={"sub_budget_lamports": 10_000_000})
        self.assertEqual(e.ex.lane.syn["sub_budget_lamports"], 10_000_000)
        self.assertEqual(e.ex.lane.reserve("m", "sg", {}, e.clock(), STAKE), "syn_budget_presend")

    def test_shared_lane_budget_and_daily_stop_still_bind(self):
        e = self.syn_env(lane_kw={"budget_lamports": 24_000_000})
        self.assertEqual(e.ex.lane.reserve("m", "sg", {}, e.clock(), STAKE), "lane_budget_presend")
        e = self.syn_env(sub="d")
        o = self.other(e)
        o.record("fee", "oo", {}, e.clock(), sol_out=100_000_000, fee_network=100_000_000)  # another bot's loss spends the lane's day
        self.feed(e, syn_row(e.clock))
        self.assertEqual((e.refusals(), e.rpc.sent), (["lane_daily_stop"], []))

    def test_lane_stop_file_still_blocks(self):
        e = self.syn_env()
        Path(e.ex.lane.cfg.stop_path).write_text("x")
        self.feed(e, syn_row(e.clock))
        self.assertEqual((e.refusals(), e.rpc.sent), (["lane_stop_file"], []))


class CrossBotAndPauseTests(SynCase):
    def test_a_mint_another_bot_traded_is_refused(self):
        e = self.syn_env()
        o = self.other(e)
        self.assertIsNone(o.reserve(syn_row(e.clock)["mint"], "s9", {}, e.clock(), STAKE))
        self.feed(e, syn_row(e.clock))
        self.assertEqual((e.refusals(), e.rpc.sent), (["syn_mint_traded_by_other_bot"], []))

    def test_a_closed_position_of_another_bot_still_counts_and_the_reserve_rechecks(self):
        e = self.syn_env()
        o = self.other(e)
        mint = syn_row(e.clock)["mint"]
        o.reserve(mint, "s9", {}, e.clock(), STAKE)
        o.record("exit", mint, {}, e.clock(), sol_in=1)
        self.assertEqual(e.ex.lane.reserve(mint, "sg", {}, e.clock(), STAKE), "syn_mint_traded_by_other_bot")  # the race: past the early check
        self.assertIsNone(e.ex.lane.reserve("fresh-mint", "sg2", {}, e.clock(), STAKE))
        self.assertIsNone(e.ex.lane.reserve("fresh-mint", "sg2", {}, e.clock(), STAKE))  # the idempotent re-send of its own row

    def test_pause_file_stops_entries_and_lifting_it_resumes(self):
        e = self.syn_env()
        pause = Path(e.ex.lane.syn["pause_path"])
        pause.write_text("pins_changed\n")
        self.feed(e, syn_row(e.clock))
        self.assertEqual((e.refusals(), e.rpc.sent), (["syn_paused"], []))
        self.assertEqual(e.ex.lane.check({}, e.clock(), STAKE), "syn_paused")
        pause.unlink()
        e.clock.t += 1_000
        self.feed(e, syn_row(e.clock))
        self.assertEqual(len(e.rpc.sent), 1)

    def test_every_lane_row_carries_the_tags(self):
        e = self.syn_env()
        self.feed(e, syn_row(e.clock, synthetic_src="rpc"))
        intent = self.rows(e, "send_intent")[0]
        for r in self.rows(e):
            if r["ev"] not in ("start", "snap"):
                self.assertEqual(r["population"], "synthetic_only")
        self.assertEqual((intent["bot_id"], intent["synthetic_src"], intent["s0_block_time_ms"]), (BOT, "rpc", S0_OK * 1000))

    def test_main_installs_the_subclass_and_restores(self):
        seen = {}

        def fake(argv=None):
            seen["cls"] = L.H5LaneExecutor
            return 0

        orig = L.H5LaneExecutor
        with mock.patch.object(L, "main", fake):
            self.assertEqual(X.main([]), 0)
        self.assertIs(seen["cls"], X.H5LaneSynExecutor)
        self.assertIs(L.H5LaneExecutor, orig)
        self.assertTrue(issubclass(X.H5LaneSynExecutor, L.H5LaneExecutor))


class ExampleConfigTests(unittest.TestCase):
    def test_example_config_parses_and_shares_the_lane_ledger(self):
        cfg = json.loads((ROOT / "scripts/mal-fast/h5-lane-syn-live.json.example").read_text())
        lc = L.LaneConfig.parse(cfg["lane"])
        self.assertEqual((lc.bot_id, lc.stake_lamports), (BOT, STAKE))
        syn = X.parse_syn_config(cfg["lane_syn"])
        self.assertEqual(syn["sub_budget_lamports"], 60_000_000)
        other = json.loads((ROOT / "scripts/mal-fast/h5-lane-live.json.example").read_text())["lane"]
        self.assertEqual((cfg["lane"]["ledger_path"], cfg["lane"]["stop_path"]), (other["ledger_path"], other["stop_path"]))  # one shared lane ledger and STOP
        self.assertTrue(cfg["intents_file"].endswith("/data/h5-shadow-lane-syn"))
        self.assertNotIn("tipauth_gate", cfg)


class StartRecordTests(SynCase):
    def test_a_real_wrapper_start_record_passes_the_executors_check(self):
        eng, out = mk_engine(S.SynEngine, hs.StaticClassifier(True, "ws"))
        rec = {"type": "start", "out_dir": "/home/x/data/h5-shadow-lane-syn", "seal": {"reason": "r", "start_ms": 1, "oracle": "lane_dec035_not_a_pick", "lane": "DEC-035"},
               "h5_look2": hs.look2_start_info(None)}
        eng.emit(rec)
        self.assertIsNone(X.start_record_reason(types(out, "start")[0]))

    def test_no_start_record_refuses_every_trigger(self):
        e = self.syn_env()
        self.feed(e, syn_row(e.clock), start=None)
        self.assertEqual((e.rpc.sent, e.ex.syn_refusals), ([], {"syn_start_missing": 1}))

    def test_each_missing_or_wrong_tag_refuses(self):
        seal = start_rec()["seal"]
        bad = {"seal.population": dict(seal={**seal, "population": "plain"}), "seal.lane": dict(seal={**seal, "lane": "DEC-036"}),
               "seal.oracle": dict(seal={**seal, "oracle": "stub_always_true"}), "bot_id": dict(bot_id="h5-lane-2"), "syn_cut_ms": dict(syn_cut_ms=S.SYN_CUT_MS - 1),
               "h5_look2.observed": dict(h5_look2={**start_rec()["h5_look2"], "observed": True, "amendment_ref": "EXP-024-Am2"}),
               "h5_look2.window": dict(h5_look2={**start_rec()["h5_look2"], "start_ms": None}), "wrapper_module": dict(wrapper_module="tools.h5_lane_shadow"),
               "wrapper_sha256": dict(wrapper_sha256="0" * 64), "out_dir": dict(out_dir="/home/x/data/h5-shadow-lane1"), "mode": dict(fresh_forward="ARENA"),
               "mode2": dict(q_star_sol=50.0)}
        for name, over in bad.items():
            e = self.syn_env(sub=f"b{name.replace('.', '_')}")
            self.feed(e, syn_row(e.clock), start=start_rec(**over))
            self.assertEqual(e.rpc.sent, [], name)
            self.assertEqual(list(e.ex.syn_refusals), [f"syn_start_bad:{name.rstrip('2')}"], name)
        e = self.syn_env(sub="missing_key")
        r = start_rec()
        del r["wrapper_sha256"]
        self.feed(e, syn_row(e.clock), start=r)
        self.assertEqual(e.rpc.sent, [])

    def test_directory_mode_follows_rolls_and_a_later_bad_start(self):
        d = self.tmp / "shadow"
        d.mkdir()
        f0, f1 = d / "h5-shadow-2026-11-06T00.jsonl", d / "h5-shadow-2026-11-06T01.jsonl"
        f0.write_text(json.dumps(start_rec()) + "\n")
        e = self.syn_env(sub="dir", intents_file=str(d))
        e.ex.intent_tick()
        e.clock.t += 1_100  # the executor re-lists the directory once a second
        f1.write_text(json.dumps(syn_row(e.clock)) + "\n")  # the roll: the start record is in the previous hour's file
        self.assertEqual(e.ex.intent_tick(), 1)
        self.assertEqual(len(e.rpc.sent), 1)
        with f1.open("a") as fh:  # the shadow restarts with the wrong wrapper
            fh.write(json.dumps(start_rec(bot_id="other")) + "\n" + json.dumps(syn_row(e.clock, mint="M2", pool="P2")) + "\n")
        e.ex.intent_tick()
        self.assertEqual(e.ex.syn_refusals, {"syn_start_bad:bot_id": 1})
        self.assertEqual(len(e.rpc.sent), 1)


class PathGuardTests(SynCase):
    def cfg(self, **syn_over) -> dict:
        d = self.tmp / "lane"
        return {"lane": self.lane_cfg(d), "state_dir": str(self.tmp / "state"), "intents_file": str(self.tmp / "shadow"), "lane_syn": self.syn_cfg(d, **syn_over)}

    def test_collisions_are_refused_at_startup(self):
        d = self.tmp / "lane"
        led = str(d / "ledger.jsonl")
        cases = {"same": dict(pause_path=str(d / "SYN-STOP")), "lane_stop_as_stop": dict(stop_path=str(d / "STOP")), "lane_stop_as_pause": dict(pause_path=str(d / "STOP")),
                 "ledger": dict(stop_path=led), "ledger_lock": dict(stop_path=led + ".lock"), "backlog": dict(pause_path=led + ".backlog.h5-lane-2"),
                 "bot_lock": dict(stop_path=led + ".bot.h5-lane-2.lock"), "state_dir": dict(pause_path=str(self.tmp / "state" / "P")),
                 "intents": dict(pause_path=str(self.tmp / "shadow" / "P")), "lane2_stop": dict(stop_path=X.H5_LANE2_PATHS[1]),
                 "lane2_state": dict(pause_path=X.H5_LANE2_PATHS[0] + "/PAUSE"), "lane2_ledger": dict(pause_path=X.H5_LANE2_PATHS[2]),
                 "via_dotdot": dict(stop_path=str(d / "x" / ".." / "STOP"))}
        for name, over in cases.items():
            with self.assertRaises(SystemExit, msg=name):
                X.parse_syn_config(self.cfg(**over)["lane_syn"], self.cfg(**over))
        X.parse_syn_config(self.cfg()["lane_syn"], self.cfg())  # the clean config passes

    def test_a_stop_directory_that_is_not_writable_is_refused(self):
        blocker = self.tmp / "afile"
        blocker.write_text("x")
        over = dict(stop_path=str(blocker / "SYN-STOP"))
        with self.assertRaisesRegex(SystemExit, "not writable"):
            X.parse_syn_config(self.cfg(**over)["lane_syn"], self.cfg(**over))
        ro = self.tmp / "ro"
        ro.mkdir()
        os.chmod(ro, 0o555)
        self.addCleanup(lambda: os.chmod(ro, 0o755))
        if os.geteuid() != 0:
            over = dict(stop_path=str(ro / "SYN-STOP"))
            with self.assertRaisesRegex(SystemExit, "not writable"):
                X.parse_syn_config(self.cfg(**over)["lane_syn"], self.cfg(**over))

    def test_the_executor_applies_the_guards(self):
        d = self.tmp / "lane"
        with self.assertRaises(SystemExit):
            self.syn_env(syn_kw={"stop_path": str(d / "STOP")})


class TrueClassPersistenceTests(SynCase):
    def test_counters_plan_state_and_decision_carry_the_true_class(self):
        e = self.syn_env()
        self.feed(e, syn_row(e.clock, synthetic_src="ws"))
        mint = syn_row(e.clock)["mint"]
        want = (True, "ws", "synthetic_only")
        plan = e.ex.counters.plans[mint]["trigger"]
        pend = e.ex.state.pending[mint]["h5"]["trigger"]
        for t in (plan, pend):
            self.assertEqual((t["synthetic"], t["synthetic_src"], t["population"]), want)
            self.assertNotIn("true_src", t)
        disk = json.loads(Path(e.ex.counters_path).read_text())["plans"][mint]["trigger"]
        self.assertEqual((disk["synthetic"], disk["synthetic_src"], disk["population"]), want)
        state_disk = json.loads(Path(e.ex.state_path).read_text())
        self.assertEqual(state_disk["pending"][mint]["h5"]["trigger"]["synthetic_src"], "ws")
        self.assertIs(state_disk["pending"][mint]["h5"]["trigger"]["synthetic"], True)
        dec = e.ledger("decision")[0]
        self.assertEqual((dec["synthetic"], dec["synthetic_src"], dec["population"]), want)
        self.assertNotIn("true_src", dec)

    def test_snap_and_start_rows_are_not_tagged_but_trade_rows_are(self):
        e = self.syn_env()
        self.feed(e, syn_row(e.clock))
        for r in self.rows(e):
            if r["ev"] in ("start", "snap"):
                self.assertNotIn("population", r)
            else:
                self.assertEqual(r["population"], "synthetic_only")


class CrossBotOverlapAlertTests(SynCase):
    def test_another_bots_row_on_a_held_mint_raises_one_alert(self):
        e = self.syn_env()
        self.feed(e, syn_row(e.clock))
        mint = syn_row(e.clock)["mint"]
        e.ex.syn_check_overlap()
        self.assertEqual(e.alerts("cross_bot_overlap"), [])
        self.other(e, "h5-lane-2").record("fee", mint, {}, e.clock(), sol_out=1, fee_network=1)
        e.clock.t += 6_000
        e.ex.syn_check_overlap()
        e.clock.t += 6_000
        e.ex.syn_check_overlap()
        (a,) = e.alerts("cross_bot_overlap")
        self.assertEqual(a["other_bot"], "h5-lane-2")
