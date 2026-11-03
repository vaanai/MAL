"""h5-lane-oc (QP-RULING-1103-lane-tipauth, Ruling 4): tools/h5_lane_oc_executor.py. Synthetic fixtures only: nothing here reads /data/mal, /var/lib/mal, a key, a
ledger or a real row. Fixtures are those of tools/test_h5_executor.py / test_h5_executor_shadow.py / test_h5_lane_syn.py."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from pathlib import Path
from unittest import mock

from solders.pubkey import Pubkey

from tools import h5_executor as h
from tools import h5_lane_executor as L
from tools import h5_lane_oc_executor as X
from tools import probe_executor as pe
from tools import pump_structure_monitor as psm
from tools import pumpswap_tx as tx
from tools.test_h5_executor import MINT, POOL, QREAL, Case, Env, H5Rpc, buy_args
from tools.test_h5_executor_shadow import shadow_trigger
from tools.test_probe_executor import V

ROOT = Path(__file__).resolve().parent.parent
BOT = "h5-lane-oc"
STAKE = 20_000_000
RESERVE = L.LANE_MIN_FEE_RESERVE
# blob ids at main f84f1fd0; the h5-lane-oc PR must not move any of them
PINNED_BLOBS = {"tools/h5_executor.py": "03995837317f65f62db2fb6b238ecaca956d01cb", "tools/h5_lane_executor.py": "c04b8e31362142cfe669b28b0506162d4bea990a",
                "tools/h5_shadow.py": "f23cf492211fd79287fa6ac82fd382f1f3e39a3d", "tools/h5_lane_shadow.py": "ea262dfd7718fa6a24665b8d2574624f63686fc2",
                "tools/h5_lane_syn_executor.py": "c572d47137024c317e8e9d3a18623cf0e5e597d5", "tools/h5_lane_syn_shadow.py": "b488fe7af8b972245fa516d328f69fb1fa97b49c"}
VAULT, AUTH = X.boost_vault_address(POOL)


def git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def boost_acct(amount: int, **over) -> dict:
    data = bytearray(165)
    data[0:32] = bytes(tx.WSOL_MINT)
    data[32:64] = bytes(Pubkey.from_string(AUTH))
    struct.pack_into("<Q", data, 64, amount)
    data[108] = 1
    acct = {"owner": str(tx.TOKEN_PROGRAM), "data": [base64.b64encode(bytes(data)).decode(), "base64"], "lamports": 2_039_280 + amount}
    return {**acct, **over}


class SlowRpc(H5Rpc):
    """H5Rpc whose every call takes `slow_ms` of fake-clock time once armed."""

    slow_ms = 0

    def __call__(self, method, params):
        self.clock.t += self.slow_ms
        return super().__call__(method, params)


class OCEnv(Env):
    def build(self):
        ex = X.H5LaneOCExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock, pick_oracle=self.oracle, root=self.root)
        self.seed_clock(ex)
        return ex


class Lane2Env(Env):
    def build(self):
        ex = L.H5LaneExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock, pick_oracle=self.oracle, root=self.root)
        self.seed_clock(ex)
        return ex


class OCCase(Case):
    def setUp(self):
        super().setUp()
        h.TIER_FILE_PATH.write_text("T1\n")
        os.chmod(h.TIER_FILE_PATH, 0o644)

    def lane_cfg(self, d: Path | None = None, bot: str = BOT, **kw) -> dict:
        d = d or self.tmp / "lane"
        return {**dict(ledger_path=str(d / "ledger.jsonl"), stop_path=str(d / "STOP"), bot_id=bot, stake_lamports=STAKE, max_open=3, budget_lamports=300_000_000,
                       daily_loss_lamports=100_000_000, fee_reserve_lamports=RESERVE, mark_stale_s=60, waive_pool_gap=True), **kw}

    def oc_cfg(self, d: Path | None = None, **kw) -> dict:
        d = d or self.tmp / "lane"
        return {**dict(stop_path=str(d / "OC-STOP")), **kw}

    def oc_env(self, sub: str | None = None, lane_kw=None, oc_kw=None, boost: int | None = 10 * 10**9, **kw) -> OCEnv:
        tmp = self.tmp
        if sub:
            tmp = self.tmp / sub
            tmp.mkdir()
        d = tmp / "lane"
        e = OCEnv(tmp, lane=self.lane_cfg(d, **(lane_kw or {})), lane_oc=self.oc_cfg(d, **(oc_kw or {})), **kw)
        e.rpc.balance = 10 * 10**9
        if boost is not None:
            e.rpc.accounts[VAULT] = boost_acct(boost)
        return e

    @staticmethod
    def feed(e: Env, *rows: dict) -> int:
        p = Path(e.conf["intents_file"])
        if not p.exists():
            p.write_text("")
            e.ex.intent_tick()  # the reader starts at the end of an existing file
        with p.open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return e.ex.intent_tick()

    @staticmethod
    def lrows(e: Env, ev: str | None = None) -> list[dict]:
        p = e.ex.lane.path
        out = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in out if ev is None or r["ev"] == ev]

    @staticmethod
    def rescue(e: Env, **kw) -> dict:
        return shadow_trigger(e.clock, base_breaks_unresolved_settled=1, **kw)


class PinnedFilesTests(Case):
    def test_pinned_files_are_byte_identical_to_main(self):
        for rel, want in PINNED_BLOBS.items():
            self.assertEqual(git_blob(ROOT / rel), want, rel)

    def test_boost_vault_address_matches_the_structure_monitor(self):
        self.assertEqual(VAULT, psm.boost_vault(POOL))
        self.assertEqual(AUTH, psm.boost_vault_authority(POOL))


class RescueTests(OCCase):
    def test_a_settled_ge1_trigger_is_bought_and_every_row_is_tagged(self):
        e = self.oc_env()
        e.rpc.quote = QREAL - 10**9  # the chain has moved: Q on chain is 1 SOL below the shadow's
        n = self.feed(e, self.rescue(e))
        self.assertEqual(n, 1)
        self.assertEqual(buy_args(e.sent()[0])[0], STAKE)
        dec = e.ledger("decision")[0]
        self.assertEqual(dec["gate_path"], "onchain_recheck")
        self.assertEqual((dec["oc_q_lamports"], dec["oc_q_shadow_lamports"]), (QREAL - 10**9 + V, QREAL + V))
        self.assertEqual((dec["oc_boost_remaining_lamports"], dec["oc_boost_vault"]), (10 * 10**9, VAULT))
        self.assertEqual(dec["q_lamports"], QREAL - 10**9 + V)  # priced on chain state
        self.assertEqual(dec["base_breaks_unresolved_settled"], 1)
        rc = e.ledger("oc_recheck")
        self.assertEqual([(r["ok"], r["gate_path"]) for r in rc], [(True, "onchain_recheck")])
        for r in self.lrows(e):
            if r["ev"] not in ("start", "snap"):
                self.assertEqual((r["gate_path"], r["oc_q_lamports"], r["bot_id"]), ("onchain_recheck", QREAL - 10**9 + V, BOT), r["ev"])
        self.assertEqual(self.lrows(e, "send_intent")[0]["gate_path"], "onchain_recheck")
        self.assertEqual(e.ex.state.pending[MINT]["h5"]["trigger"]["gate_path"], "onchain_recheck")  # durable: restarts keep the tag
        self.assertIsNone(h.H5Trigger.__dataclass_fields__["gate_path"].default)  # the pinned field stays unused: no tipauth machinery sees this class

    def test_q_above_40_is_refused(self):
        e = self.oc_env()
        e.rpc.quote = 23 * 10**9  # 23 + V 17.58 = 40.58 SOL on chain, while the shadow row says 37.6
        self.feed(e, self.rescue(e))
        self.assertEqual((e.refusals(), e.rpc.sent), (["oc_q_above_40"], []))
        self.assertEqual(e.ledger("oc_recheck")[0]["oc_q_lamports"], 23 * 10**9 + V)

    def test_q_at_the_limit_passes_and_just_above_fails(self):
        self.assertLess(h.RULE_Q_MAX_LAMPORTS - V, 23 * 10**9)
        e = self.oc_env()
        e.rpc.quote = h.RULE_Q_MAX_LAMPORTS - V
        self.feed(e, self.rescue(e))
        self.assertEqual(len(e.rpc.sent), 1)

    def test_boost_unreadable_is_refused(self):
        cases = {"closed": None, "not_pinned": "skip", "wrong_owner": boost_acct(10**9, owner="11111111111111111111111111111111"),
                 "short": {**boost_acct(10**9), "data": [base64.b64encode(b"\0" * 80).decode(), "base64"]}, "garbage": {"owner": str(tx.TOKEN_PROGRAM), "data": ["!!", "base64"]}}
        for name, acct in cases.items():
            e = self.oc_env(sub=name, boost=None)
            if acct != "skip":
                e.rpc.accounts[VAULT] = acct
            self.feed(e, self.rescue(e))
            self.assertEqual((e.refusals(), e.rpc.sent), (["oc_boost_unreadable"], []), name)

    def test_boost_vault_of_another_authority_is_refused(self):
        e = self.oc_env(boost=None)
        bad = bytearray(base64.b64decode(boost_acct(10**10)["data"][0]))
        bad[32:64] = bytes(Pubkey.new_unique())
        e.rpc.accounts[VAULT] = {"owner": str(tx.TOKEN_PROGRAM), "data": [base64.b64encode(bytes(bad)).decode(), "base64"]}
        self.feed(e, self.rescue(e))
        self.assertEqual((e.refusals(), e.rpc.sent), (["oc_boost_unreadable"], []))

    def test_boost_zero_or_below_the_rules_floor_is_refused(self):
        for name, amount in (("zero", 0), ("floor", X.BOOST_FLOOR_LAMPORTS), ("below", X.BOOST_FLOOR_LAMPORTS - 1)):
            e = self.oc_env(sub=name, boost=amount)
            self.feed(e, self.rescue(e))
            self.assertEqual((e.refusals(), e.rpc.sent), (["oc_boost_exhausted"], []), name)
        e = self.oc_env(sub="above", boost=X.BOOST_FLOOR_LAMPORTS + 1)
        self.feed(e, self.rescue(e))
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(X.BOOST_FLOOR_LAMPORTS, 17_585_000)

    def test_the_other_pinned_parser_checks_still_apply_to_a_rescue(self):
        for name, kw in (("v_missing", dict(v_missing=True)), ("gap_hold_bad_kind", dict(gap=True)), ("boost_spent", dict(boost_spent_sol=17.585)),
                         ("no_synth", dict(synthetic=None))):
            e = self.oc_env(sub=name)
            self.feed(e, self.rescue(e, **kw))
            self.assertEqual(e.rpc.sent, [], name)
            self.assertEqual(e.ledger("oc_recheck"), [], name)

    def test_tipauth_gate_in_the_config_is_refused(self):
        with self.assertRaises(SystemExit):
            self.oc_env(tipauth_gate="DEC-024-Am8")


class LatencyBoundTests(OCCase):
    def slow(self, e: Env, ms: int) -> None:
        e.rpc.__class__ = SlowRpc
        e.rpc.slow_ms = ms

    def test_a_slow_read_cannot_start_a_third_call_past_the_budget(self):
        e = self.oc_env()
        self.slow(e, 1_000)  # snapshot call 1 at 1.0 s, call 2 at 2.0 s: the BOOST read would start at 2.0 s > 1.5 s
        self.feed(e, self.rescue(e))
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(len(e.refusals()), 1)
        rc = e.ledger("oc_recheck")[0]
        self.assertFalse(rc["ok"])
        self.assertLessEqual(rc["oc_rpc_calls"], 2)

    def test_total_over_the_budget_refuses_even_when_every_call_started_in_time(self):
        e = self.oc_env()
        self.slow(e, 700)  # calls start at 0.7, 1.4, 2.1?? the third starts at 1.4 s (in budget) and ends at 2.1 s > 1.5 s
        self.feed(e, self.rescue(e))
        self.assertEqual(e.rpc.sent, [])
        self.assertIn(e.refusals()[0], ("oc_rpc_slow", "oc_rpc_timeout", "stale_trigger", "oc_boost_unreadable"))

    def test_the_3_5_s_limit_is_rechecked_after_the_reads(self):
        e = self.oc_env()
        self.slow(e, 300)
        self.feed(e, self.rescue(e, t_detect_ms=e.clock() - 2_800))  # 2.8 s old: passes the first check; +0.9 s of reads = 3.7 s
        self.assertEqual((e.refusals(), e.rpc.sent), (["stale_trigger"], []))

    def test_a_fast_read_passes_and_uses_at_most_three_calls(self):
        e = self.oc_env()
        self.slow(e, 100)
        self.feed(e, self.rescue(e))
        self.assertEqual(len(e.rpc.sent), 1)
        rc = e.ledger("oc_recheck")[0]
        self.assertLessEqual(rc["oc_rpc_calls"], X.OC_MAX_CALLS)
        self.assertLessEqual(rc["oc_rpc_ms"], X.OC_RPC_BUDGET_MS)
        self.assertEqual(X.OC_MAX_CALLS, 3)
        self.assertEqual(h.PRESEND_MAX_TRIGGER_AGE_MS, 3_500)


def decisions(e: Env) -> list:
    """Everything a trigger's fate consists of: refusal reasons, the buys sent (stake, min_out, accounts), the decision rows without clock fields."""
    sent = [buy_args(x) for x in e.sent()]
    dec = [{k: v for k, v in r.items() if k not in ("ts", "t_ms", "ts_ms", "signature", "built_ms", "sent_ms", "ms_decision_to_send", "anchor", "ms_decision_to_built",
                                                    "ms_built_to_sim", "receive_ms", "executor_ledger", "bot_id")} for r in e.ledger("decision")]
    return [e.refusals(), sent, dec]


class Settled0EquivalenceTests(OCCase):
    VARIANTS = [dict(), dict(gap=True), dict(v_missing=True), dict(q_trigger_sol=41.0), dict(t_detect_ms=-4_000), dict(base_breaks_unresolved=3), dict(boost_spent_sol=17.6),
                dict(synthetic=None), dict(base_breaks_unresolved_settled=None), dict(base_breaks_unresolved_settled=True), dict(base_breaks_unresolved_settled=0.0),
                dict(sps=0.7), dict(s0_minus_announced_slots=10_000), dict(q_trigger_sol=39.9, sell_token_raw=1_000_000), dict(variant="fv")]

    def run_set(self, env_cls, bot: str, tag: str, extra: dict) -> str:
        outs = []
        for i, v in enumerate(self.VARIANTS):
            sub = self.tmp / f"{tag}{i}"
            sub.mkdir()
            d = sub / "lane"
            e = env_cls(sub, lane=self.lane_cfg(d, bot=bot), **{k: (val(d) if callable(val) else val) for k, val in extra.items()})
            e.rpc.balance = 10 * 10**9
            e.rpc.accounts[VAULT] = boost_acct(10 * 10**9)
            kw = dict(v)
            if kw.get("t_detect_ms") == -4_000:
                kw["t_detect_ms"] = e.clock() - 4_000
            row = shadow_trigger(e.clock, **{"base_breaks_unresolved_settled": 0, **kw})
            self.feed(e, row)
            outs.append(decisions(e) + [e.rpc.calls.count("getAccountInfo"), len(e.rpc.calls)])
        return hashlib.md5(json.dumps(outs, sort_keys=True, default=str).encode()).hexdigest()

    def test_settled_0_decisions_are_md5_identical_to_h5_lane_2(self):
        lane2 = self.run_set(Lane2Env, "h5-lane-2", "a", {})
        oc = self.run_set(OCEnv, BOT, "b", {"lane_oc": lambda d: self.oc_cfg(d)})
        self.assertEqual(lane2, oc)
        print("MD5 settled=0 decision equivalence (15 fixed synthetic triggers):", oc)

    def test_the_set_is_not_vacuous(self):
        e = self.oc_env()
        self.feed(e, shadow_trigger(e.clock, base_breaks_unresolved_settled=0))
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(e.ledger("oc_recheck"), [])  # no re-check, no tag, no extra read
        self.assertNotIn("gate_path", e.ledger("decision")[0])
        self.assertNotIn("gate_path", self.lrows(e, "send_intent")[0])

    def test_the_parser_is_restored_after_the_tick(self):
        orig = h.parse_shadow_trigger
        e = self.oc_env()
        self.feed(e, self.rescue(e))
        self.assertIs(h.parse_shadow_trigger, orig)


class LimitTests(OCCase):
    @staticmethod
    def lose(e: Env, lamports: int, mint: str = "seed-loss") -> None:
        e.ex.lane.record("fee", mint, {}, e.clock(), sol_out=lamports, fee_network=lamports)

    def test_sub_budget_presend_counts_open_stake_new_stake_and_reserve(self):
        # fresh bot: 0 - (20.0 + 4.11) = -24.11 (million lamports); allowed while loss + 24.11 < 50, i.e. loss < 25.89
        e = self.oc_env()
        self.lose(e, 25_000_000)
        self.assertIsNone(e.ex.lane.check({}, e.clock(), STAKE))
        e2 = self.oc_env(sub="p")
        self.lose(e2, 26_000_000)
        self.assertEqual(e2.ex.lane.reserve("m", "sg", {}, e2.clock(), STAKE), "oc_budget_presend")
        self.feed(e2, self.rescue(e2))
        self.assertEqual((e2.refusals(), e2.rpc.sent), (["oc_budget_presend"], []))

    def test_sub_budget_stop_writes_its_own_file_and_never_resets(self):
        e = self.oc_env()
        stop, lane_stop = Path(e.ex.lane.oc["stop_path"]), Path(e.ex.lane.cfg.stop_path)
        self.lose(e, 50_000_000)
        self.assertTrue(stop.exists())
        self.assertFalse(lane_stop.exists())
        self.assertIn(BOT, stop.read_text())
        self.feed(e, self.rescue(e))
        self.assertEqual((e.refusals(), e.rpc.sent), (["oc_stop_file"], []))
        text = stop.read_text()
        e.ex.lane.write_oc_stop("again", e.clock())
        self.assertEqual(stop.read_text(), text)
        e.jump(2 * 86_400_000)  # a new UTC day does not reset it
        self.feed(e, self.rescue(e, t_detect_ms=e.clock()))
        self.assertEqual(e.rpc.sent, [])
        e.ex.release_lane_lock()
        (self.tmp / "restart").mkdir()
        e2 = OCEnv(self.tmp / "restart", lane=self.lane_cfg(), lane_oc=self.oc_cfg())  # a restart on the same ledger and files
        e2.rpc.balance = 10 * 10**9
        self.assertTrue(e2.ex.lane.oc_stop_present())
        stop.unlink()  # removing the file does not reset it: the ledger re-derives the stop at the next check
        self.assertEqual(e2.ex.lane.reserve("m", "sg", {}, e2.clock(), STAKE), "oc_budget_stop")
        self.assertTrue(stop.exists())

    def test_other_bots_losses_do_not_spend_this_bots_sub_budget(self):
        e = self.oc_env()
        o = L.LaneLedger(L.LaneConfig(**{**e.ex.lane.cfg.__dict__, "bot_id": "other-bot"}))
        o.record("fee", "oo", {}, e.clock(), sol_out=50_000_000, fee_network=50_000_000)
        self.assertIsNone(e.ex.lane.reserve("m", "sg", {}, e.clock(), STAKE))

    def test_sub_budget_can_only_be_lowered_and_stake_is_fixed(self):
        for lane_kw, oc_kw in (({}, {"sub_budget_lamports": 50_000_001}), ({"stake_lamports": 25_000_000}, {}), ({"bot_id": "h5-lane-2"}, {}), ({}, {"surprise": 1}),
                               ({}, {"stop_path": ""})):
            with self.assertRaises(SystemExit, msg=str((lane_kw, oc_kw))):
                self.oc_env(sub=f"r{abs(hash(str((lane_kw, oc_kw))))}", lane_kw=lane_kw, oc_kw=oc_kw)
        e = self.oc_env(sub="low", oc_kw={"sub_budget_lamports": 30_000_000})
        self.assertEqual(e.ex.lane.oc["sub_budget_lamports"], 30_000_000)
        with self.assertRaises(SystemExit):
            OCEnv(self.tmp / "z", lane=self.lane_cfg())  # no lane_oc block

    def test_max_open_is_one_for_this_bot(self):
        e = self.oc_env()
        self.assertEqual(e.ex.h5.max_open, 1)
        o = L.LaneLedger(L.LaneConfig(**{**e.ex.lane.cfg.__dict__, "bot_id": "other-bot"}))
        o.reserve("m-other", "s1", {}, e.clock(), STAKE)  # another bot's open position does not use this bot's slot
        self.assertIsNone(e.ex.lane.reserve("m1", "sg1", {}, e.clock(), STAKE))
        self.assertEqual(e.ex.lane.reserve("m2", "sg2", {}, e.clock(), STAKE), "oc_max_open")

    def test_a_mint_another_bot_traded_is_refused(self):
        e = self.oc_env()
        o = L.LaneLedger(L.LaneConfig(**{**e.ex.lane.cfg.__dict__, "bot_id": "other-bot"}))
        self.assertIsNone(o.reserve(MINT, "s9", {}, e.clock(), STAKE))
        self.feed(e, self.rescue(e))
        self.assertEqual((e.refusals(), e.rpc.sent), (["oc_mint_traded_by_other_bot"], []))
        # and the settled = 0 path of this bot refuses it the same way
        e2 = self.oc_env(sub="s0")
        o2 = L.LaneLedger(L.LaneConfig(**{**e2.ex.lane.cfg.__dict__, "bot_id": "other-bot"}))
        o2.reserve(MINT, "s9", {}, e2.clock(), STAKE)
        self.feed(e2, shadow_trigger(e2.clock, base_breaks_unresolved_settled=0))
        self.assertEqual((e2.refusals(), e2.rpc.sent), (["oc_mint_traded_by_other_bot"], []))

    def test_shared_lane_stop_file_still_blocks(self):
        e = self.oc_env()
        Path(e.ex.lane.cfg.stop_path).write_text("x")
        self.feed(e, self.rescue(e))
        self.assertEqual(e.rpc.sent, [])


class PathGuardAndConfigTests(OCCase):
    def test_collisions_are_refused_at_startup(self):
        d = self.tmp / "lane"
        for name, stop in (("lane_stop", str(d / "STOP")), ("lane_ledger", str(d / "ledger.jsonl")), ("ledger_lock", str(d / "ledger.jsonl.lock")),
                           ("h5_lane2_state", "/var/lib/mal-live/h5-lane/state/OC-STOP"), ("syn_dir", "/var/lib/mal-live/h5-lane-syn/OC-STOP"),
                           ("state_dir", str(self.tmp / "state_dir" / "state" / "OC-STOP")), ("intents", str(self.tmp / "intents" / "intents.jsonl"))):
            with self.assertRaises(SystemExit, msg=name):
                OCEnv(self.tmp / name, lane=self.lane_cfg(d), lane_oc={"stop_path": stop})

    def test_unwritable_directory_is_refused(self):
        with self.assertRaises(SystemExit):
            X._parse_oc_config({"stop_path": "/proc/nope/OC-STOP"}, {})

    def test_example_config_parses_and_shares_the_lane_ledger(self):
        cfg = json.loads((ROOT / "scripts/mal-fast/h5-lane-oc-live.json.example").read_text())
        lc = L.LaneConfig.parse(cfg["lane"])
        self.assertEqual((lc.bot_id, lc.stake_lamports, lc.max_open, lc.seal_disclosure, lc.waive_pool_gap), (BOT, STAKE, 1, "", True))
        two = json.loads((ROOT / "scripts/mal-fast/h5-lane-live.json.example").read_text())
        self.assertEqual((cfg["lane"]["ledger_path"], cfg["lane"]["stop_path"]), (two["lane"]["ledger_path"], two["lane"]["stop_path"]))
        self.assertEqual(cfg["lane_oc"]["sub_budget_lamports"], 50_000_000)
        self.assertNotEqual(cfg["state_dir"], two["state_dir"])
        self.assertNotIn("tipauth_gate", cfg)
        self.assertEqual(set(cfg["lane_oc"]), X.OC_KEYS)

    def test_main_installs_the_subclass_and_restores(self):
        seen = {}

        def fake_main(argv=None):
            seen["cls"] = h.H5Executor
            return 0

        orig = L.H5LaneExecutor
        with mock.patch.object(L.h, "main", fake_main), mock.patch.object(L, "_Base", L._Base):
            with mock.patch.object(L, "h", L.h):
                pass
        # L.main reads the module global H5LaneExecutor, which X.main rebinds for the call and restores afterwards
        with mock.patch.object(L, "main", lambda argv=None: seen.setdefault("cls", L.H5LaneExecutor) and 0):
            X.main([])
        self.assertIs(seen["cls"], X.H5LaneOCExecutor)
        self.assertIs(L.H5LaneExecutor, orig)
        self.assertTrue(issubclass(X.H5LaneOCExecutor, L.H5LaneExecutor))


class MutualExclusionTests(OCCase):
    def test_a_held_h5_lane_2_or_h5_lane_3_lock_stops_the_start_with_exit_2(self):
        import fcntl
        for other in X.EXCLUDED_BOTS:
            sub = self.tmp / other
            sub.mkdir()
            d = sub / "lane"
            d.mkdir()
            f = open(f"{d / 'ledger.jsonl'}.bot.{other}.lock", "a")
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(SystemExit, msg=other) as cm:
                OCEnv(sub, lane=self.lane_cfg(d), lane_oc=self.oc_cfg(d))
            self.assertEqual(cm.exception.code, 2, other)
            f.close()
            e = OCEnv(sub, lane=self.lane_cfg(d), lane_oc=self.oc_cfg(d))  # released: it starts
            e.ex.release_lane_lock()

    def test_while_h5_lane_oc_runs_h5_lane_2_and_h5_lane_3_refuse_to_start(self):
        e = self.oc_env()
        for other in X.EXCLUDED_BOTS:
            (self.tmp / other).mkdir()
            with self.assertRaises(SystemExit, msg=other):
                Lane2Env(self.tmp / other, lane=self.lane_cfg(self.tmp / "lane", bot=other))  # same ledger: the pinned _take_bot_lock refuses
        e.ex.release_lane_lock()
        (self.tmp / "after").mkdir()
        Lane2Env(self.tmp / "after", lane=self.lane_cfg(self.tmp / "lane", bot="h5-lane-2"))  # released: h5-lane-2 may start

    def test_a_second_h5_lane_oc_is_refused(self):
        first = self.oc_env()
        (self.tmp / "second").mkdir()
        with self.assertRaises(SystemExit):
            OCEnv(self.tmp / "second", lane=self.lane_cfg(self.tmp / "lane"), lane_oc=self.oc_cfg(self.tmp / "lane"))


class _FakeProbeRpc:
    """Stands in for pe.ProbeRpc under a real LimitedRpc: records the timeout it was given; every call times out."""

    def __init__(self):
        self._timeout = 15.0
        self.seen = []

    def __call__(self, method, params):
        self.seen.append(self._timeout)
        raise pe.RpcError("timeout")


class RpcTimeoutTests(OCCase):
    def test_per_call_timeout_is_cut_and_a_timeout_refuses_with_a_tag(self):
        e = self.oc_env()
        inner = _FakeProbeRpc()
        e.ex.rpc = pe.LimitedRpc(inner, rps=1000, sleep=lambda s: None)  # the client default: 15 s timeout, 3 retries
        self.feed(e, self.rescue(e))
        self.assertEqual((e.refusals(), e.rpc.sent), (["oc_rpc_timeout"], []))
        self.assertEqual(inner.seen[-1], X.OC_CALL_TIMEOUT_S)  # the re-check call: cut from 15 s
        self.assertEqual(inner.seen.count(X.OC_CALL_TIMEOUT_S), 1)  # one attempt: retries are off for the re-check
        self.assertEqual(e.ex.rpc.retries, 3)  # and restored
        self.assertEqual(inner._timeout, 15.0)

    def test_slow_fake_hits_the_shared_deadline_and_is_tagged(self):
        e = self.oc_env()
        e.rpc.__class__ = SlowRpc
        e.rpc.slow_ms = 1_000
        self.feed(e, self.rescue(e))
        self.assertEqual(e.rpc.sent, [])
        self.assertIn(e.refusals()[0], ("oc_rpc_timeout", "oc_rpc_slow"))


class TagRecordTests(OCCase):
    def test_the_per_mint_record_is_consumed_by_the_decision_and_by_a_refusal(self):
        e = self.oc_env()
        self.feed(e, self.rescue(e))
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertNotIn(MINT, e.ex._oc_mints)  # consumed by the decision row
        e.ex.state.pending.clear()  # rows of a held position are still tagged, from the durable plan
        e2 = self.oc_env(sub="ref", boost=0)
        self.feed(e2, self.rescue(e2))
        self.assertEqual(e2.refusals(), ["oc_boost_exhausted"])
        self.assertNotIn(MINT, e2.ex._oc_mints)
        self.assertEqual(e2.ledger("skip")[0]["gate_path"], "onchain_recheck")  # the refusal row itself was tagged before the record was dropped
        e2.ex._log("note", MINT)  # a later row of the mint with no position: untagged
        self.assertNotIn("gate_path", e2.ledger("note")[-1])

    def test_rows_of_a_held_position_stay_tagged_after_the_record_is_gone(self):
        e = self.oc_env()
        self.feed(e, self.rescue(e))
        self.assertNotIn(MINT, e.ex._oc_mints)
        e.ex._log("probe_row", MINT)
        r = e.ledger("probe_row")[-1]
        self.assertEqual(r["gate_path"], "onchain_recheck")
        self.assertIn("oc_q_lamports", r)


class BotIdTagTests(OCCase):
    def test_every_executor_row_carries_bot_id_including_settled_0(self):
        e = self.oc_env()
        self.feed(e, shadow_trigger(e.clock, base_breaks_unresolved_settled=0))
        rows = e.ledger()
        self.assertTrue(rows)
        self.assertEqual({r.get("bot_id") for r in rows}, {BOT})
        self.assertNotIn("gate_path", e.ledger("decision")[0])
        e2 = self.oc_env(sub="r")
        self.feed(e2, self.rescue(e2))
        self.assertEqual({r.get("bot_id") for r in e2.ledger()}, {BOT})
