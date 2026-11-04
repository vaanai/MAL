"""h5-lane-h (QP-RULING-1104-oc-feedfix, section 3.2 and tests 4-5): tools/h5_lane_heal_executor.py. Synthetic fixtures only: nothing here reads /data/mal,
/var/lib/mal, /var/lib/mal-live, a key, a tape, a tip file, a shadow dir, a ledger or a real row. The shadow side (`lane_heal`, `lane_gap_seq`) is built against
the schema of the ruling with hand-made records."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools import h5_lane_executor as L
from tools import h5_lane_heal_executor as X
from tools import test_h5_lane_oc as T
from tools.test_h5_executor import Env
from tools.test_h5_executor_shadow import S0, shadow_trigger

ROOT = Path(__file__).resolve().parent.parent
BOT = "h5-lane-h"
STAKE = 20_000_000
# blob ids at origin/main 95305b12; this PR must not move any of them
PINNED_BLOBS = {"tools/h5_executor.py": "03995837317f65f62db2fb6b238ecaca956d01cb", "tools/h5_lane_executor.py": "c04b8e31362142cfe669b28b0506162d4bea990a",
                "tools/h5_shadow.py": "f23cf492211fd79287fa6ac82fd382f1f3e39a3d", "tools/h5_lane_oc_executor.py": "68452c2343d24301b044b540c4ddf996946b2f5f"}
TAG_KEYS = set(X.HEAL_FIELDS)


def lane_heal(through: int = 5, status: str = "healed", **kw) -> dict:
    return {**{"v": 1, "status": status, "reason": "heal_ok", "kinds": ["slot_jump"], "n_gaps_full": 2, "max_interval_slots": 40, "min_slots_gap_end_to_trigger": 30,
               "tip_only_in_gaps_n": 3, "tip_only_total_n": 3, "ws_only_total_n": 0, "boost_buys_tip_only_n": 0,
               "replay": {"match": True, "n_rows": 9, "n_tip_only": 3, "spent_diff_lamports": 0}, "healed_through_seq": through,
               "follower": {"done_before": 1, "done_after": 2, "counters_equal": True}}, **kw}


def gap_row(seq=5, kind="slot_jump", flags=True) -> dict:
    row = {"type": "gap", "kind": kind, "flags_pools": flags}
    if seq is not None:
        row["lane_gap_seq"] = seq
    return row


class HealEnv(Env):
    def build(self):
        ex = X.H5LaneHealExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock, pick_oracle=self.oracle, root=self.root)
        self.seed_clock(ex)
        return ex


class HCase(T.OCCase):
    def lane_cfg(self, d: Path | None = None, bot: str = BOT, **kw) -> dict:
        return {**super().lane_cfg(d, bot=bot), "max_open": 1, "waive_pool_gap": False, **kw}

    def heal_cfg(self, d: Path | None = None, **kw) -> dict:
        d = d or self.tmp / "lane"
        return {**dict(stop_path=str(d / "HEAL-STOP")), **kw}

    def heal_env(self, sub: str | None = None, lane_kw=None, heal_kw=None, **kw) -> HealEnv:
        tmp = self.tmp
        if sub:
            tmp = self.tmp / sub
            tmp.mkdir()
        d = tmp / "lane"
        e = HealEnv(tmp, lane=self.lane_cfg(d, **(lane_kw or {})), lane_heal=self.heal_cfg(d, **(heal_kw or {})), **kw)
        e.rpc.balance = 10 * 10**9
        return e

    def trig(self, e: Env, healed: dict | None = None, **kw) -> dict:
        row = shadow_trigger(e.clock, **{"base_breaks_unresolved_settled": 0, **kw})
        if healed is not None:
            row["lane_heal"] = healed
        return row

    @staticmethod
    def sk(e: Env) -> list[str]:
        return [r["reason"] for r in e.ledger("skip")]

    @staticmethod
    def dec(e: Env) -> list[dict]:
        return e.ledger("decision")


class PinnedFilesTests(HCase):
    def test_pinned_files_are_byte_identical_to_main(self):
        for rel, want in PINNED_BLOBS.items():
            self.assertEqual(T.git_blob(ROOT / rel), want, rel)


class FixturesAreNotVacuousTests(HCase):
    def test_a_clean_trigger_buys_and_carries_the_tag(self):
        e = self.heal_env()
        self.assertEqual(self.feed(e, self.trig(e)), 1)
        self.assertEqual(len(e.rpc.sent), 1)
        d = self.dec(e)[0]
        self.assertIs(d["heal_applied"], False)
        self.assertIsNone(d["heal_kind"])
        self.assertEqual(d["feed_gap_components"], {"current": False, "pool": False, "hold": False})
        self.assertEqual(d["bot_id"], BOT)

    def test_the_pinned_lane2_refuses_a_gap_trigger_without_the_heal(self):
        e = self.heal_env()
        self.feed(e, self.trig(e, gap=True))
        self.assertEqual(self.sk(e), ["feed_gap"])
        self.assertEqual(e.rpc.sent, [])


class WaiverTests(HCase):
    def test_pool_gap_is_waived_by_a_healed_record(self):
        e = self.heal_env()
        self.feed(e, self.trig(e, lane_heal(7), gap=True))
        self.assertEqual(len(e.rpc.sent), 1)
        d = self.dec(e)[0]
        self.assertEqual((d["heal_applied"], d["heal_kind"], d["heal_reason"], d["heal_n_gaps"], d["heal_tip_only_in_gaps_n"], d["heal_boost_buys_tip_only_n"],
                          d["heal_through_seq"]), (True, "gap", "heal_ok", 2, 3, 0, 7))
        self.assertEqual(d["feed_gap_components"], {"current": False, "pool": True, "hold": False})
        self.assertIs(d["gap"], True)  # the real flag stays on the row

    def test_settled_ge1_is_treated_as_zero_only_when_healed(self):
        e = self.heal_env()
        self.feed(e, self.trig(e, lane_heal(), base_breaks_unresolved_settled=2))
        self.assertEqual(len(e.rpc.sent), 1)
        d = self.dec(e)[0]
        self.assertEqual((d["heal_kind"], d["base_breaks_unresolved_settled"]), ("break", 2))  # the real value is ledgered
        e2 = self.heal_env(sub="b")
        self.feed(e2, self.trig(e2, None, base_breaks_unresolved_settled=2))
        self.assertEqual((self.sk(e2), e2.rpc.sent), (["bad_intent:base_breaks_unresolved_settled"], []))

    def test_gap_and_break_together(self):
        e = self.heal_env()
        self.feed(e, self.trig(e, lane_heal(), gap=True, base_breaks_unresolved_settled=1))
        self.assertEqual(self.dec(e)[0]["heal_kind"], "gap+break")

    def test_healed_but_nothing_to_waive_is_not_applied(self):
        e = self.heal_env()
        self.feed(e, self.trig(e, lane_heal()))
        self.assertEqual((len(e.rpc.sent), self.dec(e)[0]["heal_applied"], self.dec(e)[0]["heal_kind"]), (1, False, None))

    def test_not_healed_statuses_and_malformed_records_are_h5_lane_2(self):
        bad = [lane_heal(status="refused"), lane_heal(status="not_needed"), lane_heal(v=2), lane_heal(v=True), lane_heal(healed_through_seq=None),
               lane_heal(healed_through_seq=True), lane_heal(healed_through_seq=-1), lane_heal(healed_through_seq="5"), "healed", [1], 7, {"status": "healed"},
               lane_heal(status="Healed")]
        for i, hv in enumerate(bad):
            e = self.heal_env(sub=f"m{i}")
            self.feed(e, self.trig(e, hv, gap=True))
            self.assertEqual((self.sk(e), e.rpc.sent), (["feed_gap"], []), repr(hv))
            e2 = self.heal_env(sub=f"n{i}")
            self.feed(e2, self.trig(e2, hv, base_breaks_unresolved_settled=1))
            self.assertEqual((self.sk(e2), e2.rpc.sent), (["bad_intent:base_breaks_unresolved_settled"], []), repr(hv))

    def test_a_bool_or_non_dict_lane_heal_never_waives(self):
        e = self.heal_env()
        self.assertFalse(X.parse_lane_heal(None))
        self.assertFalse(X.parse_lane_heal(lane_heal(v=1.0 + 1))["healed"])
        self.assertTrue(X.parse_lane_heal(lane_heal())["healed"])
        self.assertIsNone(X.parse_lane_heal("x"))
        self.assertIsNotNone(e)


class HoldTests(HCase):
    def test_hold_is_waived_only_for_covered_seqs(self):
        e = self.heal_env()
        self.feed(e, gap_row(5), self.trig(e, lane_heal(5)))
        self.assertEqual(len(e.rpc.sent), 1)
        d = self.dec(e)[0]
        self.assertEqual((d["heal_kind"], d["feed_gap_components"]), ("gap", {"current": False, "pool": False, "hold": True}))

    def test_a_seq_past_healed_through_seq_keeps_the_hold(self):
        e = self.heal_env()
        self.feed(e, gap_row(6), self.trig(e, lane_heal(5), gap=True))
        self.assertEqual((self.sk(e), e.rpc.sent), (["feed_gap"], []))

    def test_one_uncovered_row_among_covered_ones_keeps_the_hold(self):
        e = self.heal_env()
        self.feed(e, gap_row(3), gap_row(9), gap_row(4), self.trig(e, lane_heal(5)))
        self.assertEqual((self.sk(e), e.rpc.sent), (["feed_gap"], []))

    def test_a_flagged_row_without_a_seq_or_with_a_bad_seq_is_uncovered(self):
        for i, sq in enumerate([None, True, "3", 2.0]):
            e = self.heal_env(sub=f"s{i}")
            row = gap_row(None)
            if sq is not None:
                row["lane_gap_seq"] = sq
            self.feed(e, row, self.trig(e, lane_heal(5)))
            self.assertEqual((self.sk(e), e.rpc.sent), (["feed_gap"], []), repr(sq))

    def test_a_harmless_reconnect_row_neither_holds_nor_blocks(self):
        e = self.heal_env()
        self.feed(e, gap_row(99, flags=False), self.trig(e, lane_heal(5)))
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertFalse(self.dec(e)[0]["feed_gap_components"]["hold"])

    def test_the_hold_is_not_ignored_without_a_healed_record(self):
        e = self.heal_env()
        self.feed(e, gap_row(1), self.trig(e, None))
        self.assertEqual((self.sk(e), e.rpc.sent), (["feed_gap"], []))

    def test_an_old_uncovered_row_outside_the_hold_span_does_not_block(self):
        e = self.heal_env()
        self.feed(e, gap_row(50))
        e.jump(e.ex.h5.gap_hold_ms + X.HOLD_SLACK_MS + 1_000)
        e.ex.feed_last_ms = e.clock()
        self.feed(e, gap_row(4))
        self.feed(e, self.trig(e, lane_heal(5)))
        self.assertEqual(len(e.rpc.sent), 1)

    def test_break_only_trigger_with_an_uncovered_hold_keeps_the_lane2_token(self):
        e = self.heal_env()
        self.feed(e, gap_row(8), self.trig(e, lane_heal(5), base_breaks_unresolved_settled=3))
        self.assertEqual((self.sk(e), e.rpc.sent), (["bad_intent:base_breaks_unresolved_settled"], []))

    def test_hold_is_restored_after_a_waiver(self):
        e = self.heal_env()
        self.feed(e, gap_row(5), self.trig(e, lane_heal(5)))
        self.assertGreater(e.ex._gap_until_ms, e.clock())  # the pinned hold is still armed for the next, unhealed trigger


class NeverWaivedTests(HCase):
    def heal_vs_plain(self, name: str, **kw):
        """A gap-flagged trigger with a healed record gets the refusal the same trigger gets with no gap at all and no record: the heal lifts the
        gap flag only, and the next pinned check then refuses it."""
        a = self.heal_env(sub=name + "a")
        b = self.heal_env(sub=name + "b")
        pre_a = kw.pop("pre", None)
        for e, hv in ((a, lane_heal(99)), (b, None)):
            if "t_detect_ms" in kw and kw["t_detect_ms"] < 0:
                kw2 = {**kw, "t_detect_ms": e.clock() + kw["t_detect_ms"]}
            else:
                kw2 = kw
            if pre_a:
                pre_a(e)
            if hv is None:
                kw2 = {k: v for k, v in kw2.items() if k != "gap"}
            self.feed(e, self.trig(e, hv, **kw2))
        self.assertEqual(self.sk(a), self.sk(b), name)
        self.assertEqual(len(a.rpc.sent), len(b.rpc.sent), name)
        return a, b

    def test_g_cur_is_never_waived(self):
        for kw in (dict(gap=True), dict(base_breaks_unresolved_settled=2)):
            a = self.heal_env(sub=f"g{len(kw)}{list(kw)[0]}")
            a.ex.on_feed_status(True)
            self.feed(a, self.trig(a, lane_heal(99), **kw))
            self.assertEqual(a.rpc.sent, [], kw)
            self.assertEqual(self.sk(a)[-1:], ["feed_gap"] if "gap" in kw else ["bad_intent:base_breaks_unresolved_settled"], kw)
            self.assertTrue(all(r.get("heal_applied") is not True for r in a.ledger()), kw)

    def test_g_cur_with_pool_gap_and_hold_is_refused_and_not_applied(self):
        a = self.heal_env()
        a.ex.on_feed_status(True)
        self.feed(a, gap_row(1), self.trig(a, lane_heal(99), gap=True))
        self.assertEqual((self.sk(a)[-1:], a.rpc.sent), (["feed_gap"], []))
        self.assertEqual([r["heal_applied"] for r in a.ledger("skip") if "heal_applied" in r], [False])
        self.assertTrue(self.ledger_comp(a)["current"])

    @staticmethod
    def ledger_comp(e: Env) -> dict:
        return [r for r in e.ledger("skip") if "feed_gap_components" in r][-1]["feed_gap_components"]

    def test_every_other_refusal_is_unchanged_with_a_healed_record(self):
        cases = {"stale_trigger": dict(t_detect_ms=-4_000, gap=True), "sps": dict(sps=0.7, gap=True), "s0_minus_announced": dict(s0_minus_announced_slots=10_000, gap=True),
                 "q_above_rule": dict(q_trigger_sol=41.0, gap=True), "boost_spent": dict(boost_spent_sol=17.6, gap=True), "v_missing": dict(v_missing=True, gap=True),
                 "synthetic": dict(synthetic=None, gap=True), "window": dict(s0=S0 - 5_000, gap=True)}
        for name, kw in cases.items():
            a, b = self.heal_vs_plain(name, **kw)
            self.assertEqual(a.rpc.sent, [], name)
            self.assertTrue(self.sk(a), name)
            self.assertNotEqual(self.sk(a), ["feed_gap"], name)

    def test_feed_stale_and_sell_stuck_are_never_waived(self):
        for i, (cfg, why) in enumerate([(dict(feed_heartbeat_max_age_ms=5_000), "feed_stale")]):
            e = self.heal_env(sub=f"fs{i}", **cfg)
            e.ex.feed_last_ms = e.clock() - 60_000
            self.feed(e, self.trig(e, lane_heal(99), gap=True))
            self.assertEqual((self.sk(e), e.rpc.sent), ([why], []))
            self.assertTrue(all(r.get("heal_applied") is True for r in e.ledger("skip")))  # the gap part was lifted; feed_stale then refused

    def test_lane_limits_still_apply_to_a_healed_trigger(self):
        e = self.heal_env()
        self.feed(e, self.trig(e, lane_heal(), gap=True))
        self.assertEqual(len(e.rpc.sent), 1)
        e.rpc.accounts.clear()
        other = dict(self.trig(e, lane_heal(), gap=True), mint="Mint2222222222222222222222222222222222222222")
        self.feed(e, other)
        self.assertEqual(len(e.rpc.sent), 1)  # max open 1 (or the base's own limits): never a second buy



class EquivalenceTests(HCase):
    """Test 4: with lane_heal stripped, decisions are md5-equal to the pinned h5-lane-2 (waive_pool_gap false)."""
    VARIANTS = [dict(), dict(gap=True), dict(v_missing=True), dict(q_trigger_sol=41.0), dict(t_detect_ms=-4_000), dict(base_breaks_unresolved=3), dict(boost_spent_sol=17.6),
                dict(synthetic=None), dict(base_breaks_unresolved_settled=None), dict(base_breaks_unresolved_settled=True), dict(base_breaks_unresolved_settled=2),
                dict(base_breaks_unresolved_settled=2, gap=True), dict(sps=0.7), dict(s0_minus_announced_slots=10_000), dict(q_trigger_sol=39.9, sell_token_raw=1_000_000),
                dict(variant="fv"), dict(pre=[gap_row(1)]), dict(pre=[gap_row(None)], gap=True), dict(pre=[gap_row(2, flags=False)]), dict(pre=[gap_row(3), gap_row(4)], gap=True),
                dict(lane_heal_bad="refused", gap=True), dict(lane_heal_bad="refused", base_breaks_unresolved_settled=1), dict(lane_heal_bad="v2", gap=True),
                dict(lane_heal_bad="noseq", pre=[gap_row(1)], gap=True), dict(lane_heal_bad="str", base_breaks_unresolved_settled=1)]
    BAD = {"refused": lane_heal(status="refused"), "v2": lane_heal(v=2), "noseq": lane_heal(healed_through_seq=None), "str": "healed"}

    def run_set(self, env_cls, bot: str, tag: str, extra: dict) -> tuple[str, list]:
        outs = []
        for i, v in enumerate(self.VARIANTS):
            sub = self.tmp / f"{tag}{i}"
            sub.mkdir()
            d = sub / "lane"
            e = env_cls(sub, lane=self.lane_cfg(d, bot=bot), **{k: (val(d) if callable(val) else val) for k, val in extra.items()})
            e.rpc.balance = 10 * 10**9
            kw = dict(v)
            pre = kw.pop("pre", [])
            bad = kw.pop("lane_heal_bad", None)
            if kw.get("t_detect_ms") == -4_000:
                kw["t_detect_ms"] = e.clock() - 4_000
            row = shadow_trigger(e.clock, **{"base_breaks_unresolved_settled": 0, **kw})
            if bad is not None and env_cls is HealEnv:
                row["lane_heal"] = self.BAD[bad]  # a not-healed record: must read exactly as no record
            self.feed(e, *pre, row)
            dec = [{k: val for k, val in r.items() if k not in TAG_KEYS and k not in ("ts", "t_ms", "ts_ms", "signature", "built_ms", "sent_ms", "ms_decision_to_send", "anchor",
                                                                                 "ms_decision_to_built", "ms_built_to_sim", "receive_ms", "executor_ledger", "bot_id")}
                   for r in e.ledger("decision")]
            outs.append([e.refusals(), [T.buy_args(x) for x in e.sent()], dec, e.rpc.calls.count("getAccountInfo"), len(e.rpc.calls)])
        return hashlib.md5(json.dumps(outs, sort_keys=True, default=str).encode()).hexdigest(), outs

    def test_stripped_decisions_are_md5_identical_to_h5_lane_2(self):
        lane2, o2 = self.run_set(T.Lane2Env, "h5-lane-2", "a", {})
        heal, oh = self.run_set(HealEnv, BOT, "b", {"lane_heal": lambda d: self.heal_cfg(d)})
        self.assertEqual(lane2, heal)
        self.assertEqual(o2, oh)
        print("MD5 stripped-lane_heal decision equivalence (%d fixed synthetic triggers):" % len(self.VARIANTS), heal)

    def test_the_set_is_not_vacuous(self):
        _m, outs = self.run_set(T.Lane2Env, "h5-lane-2", "c", {})
        n_buy = sum(1 for o in outs if o[1])
        self.assertGreaterEqual(n_buy, 4)
        self.assertGreaterEqual(len({tuple(o[0]) for o in outs}), 6)

    def test_the_parser_and_tail_are_restored_after_the_tick(self):
        po, to = h.parse_shadow_trigger, h.tail_lines
        e = self.heal_env()
        self.feed(e, self.trig(e, lane_heal(), gap=True))
        self.assertIs(h.parse_shadow_trigger, po)
        self.assertIs(h.tail_lines, to)


class RowTagTests(HCase):
    def test_every_row_is_tagged_with_bot_id_and_the_decision_skip_and_lane_rows_with_the_heal_fields(self):
        e = self.heal_env()
        self.feed(e, self.trig(e, lane_heal(7), gap=True))
        for r in e.ledger():
            self.assertEqual(r.get("bot_id"), BOT, r.get("kind"))
        for r in e.ledger("decision"):
            self.assertTrue(TAG_KEYS <= set(r), r)
        send = self.lrows(e, "send_intent")
        self.assertEqual(len(send), 1)
        self.assertTrue(TAG_KEYS <= set(send[0]))
        self.assertEqual((send[0]["heal_applied"], send[0]["heal_kind"], send[0]["bot_id"]), (True, "gap", BOT))
        self.assertIs(send[0]["pool_gap_waived"], False)  # the pinned lane-2 waiver is not what lifted it
        e2 = self.heal_env(sub="r")
        self.feed(e2, self.trig(e2, None, gap=True))
        sk = e2.ledger("skip")
        self.assertEqual(len(sk), 1)
        self.assertTrue(TAG_KEYS <= set(sk[0]))
        self.assertEqual((sk[0]["heal_applied"], sk[0]["reason"], sk[0]["bot_id"]), (False, "feed_gap", BOT))
        self.assertEqual(sk[0]["feed_gap_components"], {"current": False, "pool": True, "hold": False})

    def test_later_rows_of_a_held_position_keep_the_tag_and_a_new_trigger_does_not_overwrite_it(self):
        e = self.heal_env()
        self.feed(e, self.trig(e, lane_heal(), gap=True))
        tag = self.lrows(e, "send_intent")[0]["heal_kind"]
        self.assertEqual(tag, "gap")
        self.feed(e, self.trig(e, None))  # same mint again, unhealed: refused, the held position's tag stays
        self.assertEqual(e.ex.lane.tags[T.MINT]["heal_kind"], "gap")
        # after a restart the durable plan gives the tag back
        plan = (e.ex.state.pending.get(T.MINT) or e.ex.state.open.get(T.MINT) or {}).get("h5", {}).get("trigger", {})
        self.assertIs(plan.get("heal_applied"), True)
        self.assertEqual(e.ex._heal_tag(T.MINT)["heal_through_seq"] if e.ex._heal_tag(T.MINT) else None, 5)


class LockAndConfigTests(HCase):
    def test_a_held_lock_of_any_excluded_book_stops_the_start_with_exit_2(self):
        for other in X.EXCLUDED_BOTS:
            sub = self.tmp / other
            sub.mkdir()
            d = sub / "lane"
            d.mkdir()
            f = open(f"{d / 'ledger.jsonl'}.bot.{other}.lock", "a")
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(SystemExit, msg=other) as cm:
                HealEnv(sub, lane=self.lane_cfg(d), lane_heal=self.heal_cfg(d))
            self.assertEqual(cm.exception.code, 2, other)
            f.close()
            e = HealEnv(sub, lane=self.lane_cfg(d), lane_heal=self.heal_cfg(d))  # released: it starts
            e.ex.release_lane_lock()

    def test_the_excluded_set_is_lane_2_lane_3_and_lane_oc(self):
        self.assertEqual(set(X.EXCLUDED_BOTS), {"h5-lane-2", "h5-lane-3", "h5-lane-oc"})

    def test_while_h5_lane_h_runs_the_other_lane_books_refuse_to_start(self):
        e = self.heal_env()
        d = self.tmp / "lane"
        for other in ("h5-lane-2", "h5-lane-3"):
            (self.tmp / other).mkdir()
            with self.assertRaises(SystemExit, msg=other):
                T.Lane2Env(self.tmp / other, lane=T.OCCase.lane_cfg(self, d, bot=other))
        (self.tmp / "oc").mkdir()
        with self.assertRaises(SystemExit):
            T.OCEnv(self.tmp / "oc", lane=T.OCCase.lane_cfg(self, d, bot="h5-lane-oc"), lane_oc=T.OCCase.oc_cfg(self, d))
        (self.tmp / "second").mkdir()
        with self.assertRaises(SystemExit):
            HealEnv(self.tmp / "second", lane=self.lane_cfg(d), lane_heal=self.heal_cfg(d))
        e.ex.release_lane_lock()
        (self.tmp / "after").mkdir()
        T.Lane2Env(self.tmp / "after", lane=T.OCCase.lane_cfg(self, d, bot="h5-lane-2"))  # released: h5-lane-2 may start

    def test_waive_pool_gap_true_is_refused_at_startup(self):
        d = self.tmp / "lane"
        with self.assertRaises(SystemExit) as cm:
            HealEnv(self.tmp, lane=self.lane_cfg(d, waive_pool_gap=True), lane_heal=self.heal_cfg(d))
        self.assertIn("waive_pool_gap", str(cm.exception))
        for bad in (1, "false", None):
            (self.tmp / f"w{bad}").mkdir()
            with self.assertRaises(SystemExit):
                HealEnv(self.tmp / f"w{bad}", lane=self.lane_cfg(d, waive_pool_gap=bad), lane_heal=self.heal_cfg(d))

    def test_bot_id_stake_tipauth_and_collisions_are_refused(self):
        d = self.tmp / "lane"
        cases = [("bot", dict(lane=self.lane_cfg(d, bot="h5-lane-2")), self.heal_cfg(d)), ("stake", dict(lane=self.lane_cfg(d, stake_lamports=10_000_000)), self.heal_cfg(d)),
                 ("tipauth", dict(lane=self.lane_cfg(d), tipauth_gate="x"), self.heal_cfg(d)), ("sub0", dict(lane=self.lane_cfg(d)), self.heal_cfg(d, sub_budget_lamports=0)),
                 ("sub_big", dict(lane=self.lane_cfg(d)), self.heal_cfg(d, sub_budget_lamports=50_000_001)), ("extra", dict(lane=self.lane_cfg(d)), self.heal_cfg(d, x=1)),
                 ("lane_stop", dict(lane=self.lane_cfg(d)), {"stop_path": str(d / "STOP")}), ("ledger", dict(lane=self.lane_cfg(d)), {"stop_path": str(d / "ledger.jsonl")}),
                 ("oc_dir", dict(lane=self.lane_cfg(d)), {"stop_path": "/var/lib/mal-live/h5-lane-oc/HEAL-STOP"}),
                 ("syn_dir", dict(lane=self.lane_cfg(d)), {"stop_path": "/var/lib/mal-live/h5-lane-syn/HEAL-STOP"}),
                 ("h5_lane2", dict(lane=self.lane_cfg(d)), {"stop_path": "/var/lib/mal-live/h5-lane/state/HEAL-STOP"})]
        for name, kw, hc in cases:
            (self.tmp / name).mkdir()
            with self.assertRaises(SystemExit, msg=name):
                HealEnv(self.tmp / name, lane_heal=hc, **kw)
        (self.tmp / "nokey").mkdir()
        with self.assertRaises(SystemExit):
            HealEnv(self.tmp / "nokey", lane=self.lane_cfg(d))

    def test_example_config(self):
        cfg = json.loads((ROOT / "scripts/mal-fast/h5-lane-heal-dry.json.example").read_text())
        lc = L.LaneConfig.parse(cfg["lane"])
        self.assertEqual((lc.bot_id, lc.stake_lamports, lc.max_open, lc.waive_pool_gap), (BOT, STAKE, 1, False))
        self.assertEqual(cfg["lane_heal"]["sub_budget_lamports"], 50_000_000)
        self.assertEqual(set(cfg["lane_heal"]), X.HEAL_KEYS)
        self.assertNotIn("tipauth_gate", cfg)
        self.assertEqual(cfg["mode"], "dryrun")
        oc = json.loads((ROOT / "scripts/mal-fast/h5-lane-oc-live.json.example").read_text())
        self.assertNotEqual(cfg["state_dir"], oc["state_dir"])
        self.assertNotEqual(cfg["lane_heal"]["stop_path"], oc["lane_oc"]["stop_path"])
        self.assertEqual((cfg["lane"]["ledger_path"], cfg["lane"]["stop_path"]), (oc["lane"]["ledger_path"], oc["lane"]["stop_path"]))

    def test_main_installs_the_subclass_and_restores(self):
        seen = {}
        orig = L.H5LaneExecutor
        with mock.patch.object(L, "main", lambda argv=None: seen.setdefault("cls", L.H5LaneExecutor) and 0):
            X.main([])
        self.assertIs(seen["cls"], X.H5LaneHealExecutor)
        self.assertIs(L.H5LaneExecutor, orig)
        self.assertTrue(issubclass(X.H5LaneHealExecutor, L.H5LaneExecutor))


class LimitTests(HCase):
    def lose(self, e: Env, lamports: int, mint: str = "seed-loss") -> None:
        e.ex.lane.record("fee", mint, {}, e.clock(), sol_out=lamports, fee_network=lamports)

    def test_sub_budget_stop_is_written_never_removed_and_blocks(self):
        e = self.heal_env()
        stop = Path(e.ex.lane.heal["stop_path"])
        self.assertFalse(stop.exists())
        self.lose(e, 50_000_000)
        self.assertTrue(stop.exists())
        self.assertIn(BOT, stop.read_text())
        self.feed(e, self.trig(e, lane_heal(), gap=True))
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(self.sk(e), ["heal_stop_file"])
        stop.unlink()  # deleted by hand: re-derived from the ledger on the next check
        e.ex.lane.check({}, e.clock(), STAKE)
        self.assertEqual(e.ex.lane.heal_evaluate(e.ex.lane.read(), None, {}, e.clock(), STAKE), "heal_budget_stop")
        self.assertTrue(stop.exists())

    def test_other_bots_losses_do_not_spend_this_bots_budget(self):
        e = self.heal_env()
        e.ex.lane.record("fee", "m-other", {}, e.clock(), sol_out=60_000_000, fee_network=60_000_000)
        rows = e.ex.lane.read()
        self.assertEqual(e.ex.lane.bot_view(rows, {}, e.clock()).pnl, -60_000_000)  # the fixture row is this bot's own
        for r in rows:
            if r.get("mint") == "m-other":
                r["bot_id"] = "h5-lane-2"
        self.assertEqual(e.ex.lane.bot_view(rows, {}, e.clock()).pnl, 0)

    def test_max_open_is_one_and_stake_is_fixed(self):
        e = self.heal_env()
        self.assertEqual(e.ex.h5.max_open, 1)
        self.assertEqual(e.ex.h5.stake_lamports, STAKE)
        self.assertEqual(e.ex.lane.heal_evaluate([], None, {}, e.clock(), STAKE + 1), "heal_stake_not_fixed")

    def test_a_mint_another_bot_traded_is_refused(self):
        e = self.heal_env()
        p = e.ex.lane.path
        p.write_text(json.dumps({"ev": "send_intent", "ts_ms": e.clock(), "mint": T.MINT, "bot_id": "h5-lane-2", "stake_lamports": STAKE}) + "\n")
        self.feed(e, self.trig(e, lane_heal(), gap=True))
        self.assertEqual(e.rpc.sent, [])
        self.assertTrue(self.sk(e))
        self.assertNotEqual(self.sk(e), ["feed_gap"])
        self.assertEqual(e.ex.lane.heal_evaluate(e.ex.lane.read(), T.MINT, {}, e.clock(), STAKE), "heal_mint_traded_by_other_bot")

    def test_shared_lane_stop_file_still_blocks(self):
        e = self.heal_env()
        Path(e.ex.lane.cfg.stop_path).write_text("x\n")
        self.feed(e, self.trig(e, lane_heal(), gap=True))
        self.assertEqual(e.rpc.sent, [])
