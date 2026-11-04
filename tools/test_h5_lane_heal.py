"""tools.h5_lane_heal (A-R tip-replay heal, shadow side; QP-RULING-1104-oc-feedfix s3.1, tests 1-3 of s3.3). Synthetic fixtures only: no tape, no tip file, no
follower file, no network, no outcome field is read. The follower status.json is a temp file written by the test."""
from __future__ import annotations

import collections
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_lane_heal as hl
from tools import h5_lane_heal_shadow as hsh
from tools import h5_lane_shadow as ls
from tools import h5_shadow as hs
from tools.test_h5_shadow import MINT, POOL, SOL, Tape

STOCK = hs.Engine
LANE_SHADOW_BLOB = "ea262dfd7718fa6a24665b8d2574624f63686fc2"  # pinned by test_h5_lane_oc / test_h5_lane_executor / test_h5_lane_syn
PINNED_BLOBS = ("tools/h5_shadow.py", "tools/h5_executor.py", "tools/h5_lane_executor.py", "tools/h5_lane_oc_executor.py")
ROOT = Path(__file__).resolve().parent.parent
PDA = "PDA_" + POOL


class FakeTip:
    def __init__(self, hw_slot=1300, status="ok"):
        self.hw_slot, self.st, self.err, self.c = hw_slot, status, None, collections.Counter()
        self.is_pending = False

    def pending(self):
        return self.is_pending

    def status(self, feed_hw):
        return self.st

    def stats(self):
        return {"hw_slot": self.hw_slot}


def mk(cls, status_path=None, tip=None, live_sps=False):
    out: list[dict] = []
    now = [1_800_000_000_000]
    sps = None if live_sps else (lambda p: 0.4)
    if cls is STOCK:
        eng = cls(out.append, wall=lambda: now[0], sps_fn=sps, pda_fn=lambda pool: "PDA_" + pool, classifier=hs.StaticClassifier(False),
                  seal_start_ms=None, h5_look2_start_ms=None)
    else:
        eng = hl.build_engine_class(status_path)(out.append, wall=lambda: now[0], sps_fn=sps, pda_fn=lambda pool: "PDA_" + pool,
                                                   classifier=hs.StaticClassifier(False), seal_start_ms=None, h5_look2_start_ms=None)
    eng.now = now
    eng.tip = tip
    return eng, out


def write_status(path, done, gaps=0, pinned=True, event_v=True):
    with open(path, "w") as f:
        json.dump({"last_done_slot": done, "gaps": gaps, "unresolved_dropped": 0, "lookup_failures": 0, "backlog_jumps": 0, "decoder_blobs_pinned": pinned,
                   "trade_event_v": event_v}, f)


class Scn:
    """One pool's full tape (every row real) plus the set of row indexes hidden from the websocket."""

    def __init__(self, pool=POOL, pda=None):
        self.t = Tape(pool=pool)
        self.pool, self.pda, self.hid, self.ws_only = pool, pda or ("PDA_" + pool), set(), set()

    def row(self, slot, side, trader, sol, hide=False, ws_only=False, **kw):
        r = self.t.row(slot, side, trader, sol, **kw)
        if hide:
            self.hid.add(len(self.t.rows) - 1)
        if ws_only:
            self.ws_only.add(len(self.t.rows) - 1)
        return r

    def finish(self):
        for i, r in enumerate(self.t.rows):
            r["event_index"], r["tx_index"] = 0, i
            r["signature"] = f"{self.pool}-{i}"
        return self

    @property
    def rows(self):
        return self.t.rows

    def ws_rows(self):
        return [r for i, r in enumerate(self.rows) if i not in self.hid]

    def tip_rows(self):
        return [dict(r) for i, r in enumerate(self.rows) if i not in self.ws_only]


def base(case, pool=POOL):
    s = Scn(pool)
    P = s.pda
    s.row(1000, "buy", "A", SOL // 10)
    s.row(1020, "buy", P, SOL // 2)
    s.row(1045, "buy", P, SOL // 2)
    s.row(1070, "buy", P, SOL // 2)
    s.row(1095, "buy", "B", SOL // 20)
    if case == "a":
        s.row(1100, "buy", "C", SOL // 10, hide=True)
        s.row(1110, "buy", P, SOL // 2, hide=True)
        s.row(1130, "sell", P, SOL // 10, hide=True)  # the vault identity's own sell is never a trigger
    elif case == "b":
        s.row(1100, "buy", "C", SOL // 10, hide=True)
        s.row(1120, "sell", "S1", SOL // 5, hide=True)  # the first qualifying sell
    elif case == "c":
        for k in range(9):
            s.row(1100 + 6 * k, "buy", P, 2 * SOL, hide=True)  # BOOST buys that cross 0.999 x budget
    elif case == "d":
        s.row(1120, "buy", "C", SOL // 20, hide=True)  # one hidden base-chain print
    elif case == "g":
        s.row(1120, "buy", "C", SOL // 20, hide=True)  # tip-only
        s.row(1120, "buy", "W", SOL // 20, ws_only=True)  # ws-only, same slot
    s.row(1160, "buy", "D", SOL // 20)
    s.row(1180, "buy", "E", SOL // 20)
    q_tot = s.t.q + s.t.v
    s.row(1200, "sell", "DUMPER", int(q_tot - 35 * SOL))
    return s.finish()


def feed(eng, scn, status_path=None, after=(1170, 0), tip_rows=None, ws_script=None):
    """Websocket rows in order; before the last row (the trigger print) the follower moves on and the tip rows arrive (the tip lags the feed)."""
    rows = scn.ws_rows()
    eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
    for r in rows[:-1]:
        eng.on_trade(dict(r))
    if status_path:
        write_status(status_path, *after)
    for r in (scn.tip_rows() if tip_rows is None else tip_rows):
        eng.on_tip_row(r)
    eng.on_trade(dict(rows[-1]))


def trigs(out):
    return [r for r in out if r["type"] == "trigger"]


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.sp = os.path.join(self.d.name, "status.json")
        write_status(self.sp, 1050)

    def case(self, name, after=(1170, 0), tip=None, keep=False, **kw):
        if not keep:
            write_status(self.sp, 1050)
        scn = base(name)
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, tip or FakeTip())
        feed(eng, scn, self.sp, after=after, **kw)
        return scn, eng, out


class Test1Equivalence(Base):
    def script(self, eng, scn):
        feed(eng, scn, self.sp if getattr(eng, "_lane_heal", False) else None)
        eng.tick(eng.last_event_ms + 25_000)  # a silence gap
        eng.feed_restart(eng.last_event_ms + 26_000, "test")
        eng.close_all("t")

    def run_pair(self, name):
        scn = base(name)
        write_status(self.sp, 1050)
        a, out_a = mk(STOCK, tip=FakeTip())
        self.script(a, scn)
        write_status(self.sp, 1050)
        b, out_b = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        self.script(b, scn)
        return out_a, out_b

    def test_heal_on_identical_after_deleting_the_two_new_keys(self):
        for name in ("a", "b", "c", "d", "g"):
            out_a, out_b = self.run_pair(name)
            self.assertEqual(len(out_a), len(out_b), name)
            n_seq = n_heal = 0
            for ra, rb in zip(out_a, out_b):
                rb2 = {k: v for k, v in rb.items() if k not in ("lane_heal", "lane_gap_seq")}
                self.assertEqual(json.dumps(ra), json.dumps(rb2), name)  # same keys, same order, same bytes
                if "lane_gap_seq" in rb:
                    n_seq += 1
                    self.assertEqual(list(rb)[-1], "lane_gap_seq")
                    self.assertTrue(rb["flags_pools"])
                if "lane_heal" in rb:
                    n_heal += 1
                    self.assertEqual(list(rb)[-1], "lane_heal")
                    self.assertEqual(rb["type"], "trigger")
            self.assertGreaterEqual(n_seq, 3, name)
            self.assertEqual(n_heal, len(trigs(out_a)))
            self.assertGreaterEqual(n_heal, 2)

    def test_lane_heal_keys_exact_and_no_tip_row_value(self):
        scn, eng, out = self.case("a")
        (lh,) = [r["lane_heal"] for r in trigs(out) if r["variant"] == "pv"]
        self.assertEqual(tuple(lh), hl.LANE_HEAL_KEYS)
        self.assertEqual(set(lh["replay"]), {"match", "n_rows", "n_tip_only", "spent_diff_lamports"})
        self.assertEqual(set(lh["follower"]), {"done_before", "done_after", "counters_equal"})
        blob = json.dumps(lh)
        for r in scn.rows:
            self.assertNotIn(r["signature"], blob)
        self.assertNotIn(MINT, blob)

    def test_gap_rows_flagged_only_get_seq_monotone(self):
        scn, eng, out = self.case("a")
        seqs = [r["lane_gap_seq"] for r in out if r["type"] == "gap"]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))
        # an unflagged socket_reconnect gap row stays untouched
        e, o = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        e._gap("socket_reconnect", flag_pools=False, at_ms=1)
        self.assertNotIn("lane_gap_seq", o[0])

    def test_heal_off_wrapper_does_not_touch_engine(self):
        seen = []
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(hs, "main", side_effect=lambda r: seen.append(hs.Engine) or 0), \
                mock.patch.object(hs, "cap_pick_oracle_from_env", hs.cap_pick_oracle_from_env):
            os.makedirs(f"{home}/data")
            out = f"{home}/data/h5-shadow-lane9"
            self.assertEqual(ls.main(["--lane", "DEC-035", "--out-dir", out]), 0)
            self.assertIs(seen[-1], STOCK)
            n = len(seen)
            self.assertEqual(hsh.main(["--lane", "DEC-035", "--out-dir", out]), 2)  # no --lane-heal
            self.assertEqual(hsh.main(["--lane", "DEC-035", "--lane-heal", "DEC-035", "--out-dir", out]), 2)  # needs --tip-dir
            self.assertEqual(hsh.main(["--lane", "DEC-035", "--lane-heal", "DEC-036", "--tip-dir", "/x", "--out-dir", out]), 2)
            self.assertEqual(len(seen), n)
            try:
                self.assertEqual(hsh.main(["--lane", "DEC-035", "--lane-heal", "DEC-035", "--tip-dir", "/x", "--out-dir", out]), 0)
                self.assertTrue(getattr(seen[-1], "_lane_heal", False))
                self.assertTrue(issubclass(seen[-1], STOCK))
            finally:
                hs.Engine = STOCK

    def test_lane_shadow_blob_is_pinned(self):
        h = subprocess.run(["git", "hash-object", "tools/h5_lane_shadow.py"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(h.stdout.strip(), LANE_SHADOW_BLOB)

    def test_script_selects_heal_module_only_with_the_env(self):
        txt = (ROOT / "scripts/research/h5-lane-shadow.sh").read_text()
        self.assertIn("ENTRY=tools.h5_lane_shadow\n", txt)
        self.assertIn('-m "$ENTRY" --lane DEC-035', txt)
        self.assertEqual(txt.count("ENTRY=tools.h5_lane_heal_shadow"), 1)

    def test_pinned_blobs_unchanged_vs_origin_main(self):
        for f in PINNED_BLOBS:
            r = subprocess.run(["git", "rev-parse", f"origin/main:{f}"], cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest("origin/main not fetched")
            h = subprocess.run(["git", "hash-object", f], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), h.stdout.strip(), f)

    def test_heal_without_tip_adds_nothing(self):
        scn = base("a")
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, None)
        feed(eng, scn, None, tip_rows=[])
        self.assertTrue(trigs(out))
        self.assertTrue(all("lane_heal" not in r for r in out))


def synthetic_hour(n_pools=14):
    """n_pools pools in disjoint slot ranges (own mint each, no slot jump inside a pool): varied BOOST identity (vault PDA / behavioural), buy counts and sizes."""
    rows, pools = [], []
    for k in range(n_pools):
        pool = f"Pool{k:03d}"
        off = 220 * k
        t = Tape(pool=pool, slot0=1000 + off, ts0=1_800_000_000 + int(88 * k), q=(1500 + 37 * k) * SOL // 100, b=200_000_000_000_000 - 10**9 * k)
        ident = "PDA_" + pool if k % 2 == 0 else "BOOSTER"
        t.row(1000 + off, "buy", "A", SOL // 10)
        for i, sl in enumerate(range(1020, 1200, 20)):
            if i < 3 + k % 3:
                t.row(sl + off, "buy", ident, SOL // 2 + 10_000_000 * (k % 4))
            else:
                t.row(sl + off, "buy", f"T{i}", SOL // 40)
        q_tot = t.q + t.v
        t.row(1200 + off, "sell", "DUMPER", int(q_tot - (33 + k % 5) * SOL))
        t.row(1210 + off, "buy", "Z", SOL // 20)
        for i, r in enumerate(t.rows):
            r["event_index"], r["tx_index"], r["signature"], r["mint"] = 0, i, f"{pool}-{i}", f"Mint{k:03d}"
        rows.extend(t.rows)
        pools.append(pool)
    return rows, pools


class Test2REq(Base):
    def test_replay_reproduces_every_stock_trigger(self):
        """Synthetic fixture (no allowed real exploration hour was used; see the PR body): 14 pools, 2 variants, >= 10 triggers. Every trigger of the stock-equal engine
        carries a CONTROL replay (status not_needed) that must match on variant, signature, event_index, slot, boost_src and boost_spent."""
        rows, pools = synthetic_hour()
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip(hw_slot=10**9))
        for p in pools:
            eng.on_create_pool(p, "Mint" + p[4:], hs.WSOL_MINT, 0, 0)
        stock, out_s = mk(STOCK, tip=FakeTip(hw_slot=10**9))
        for p in pools:
            stock.on_create_pool(p, "Mint" + p[4:], hs.WSOL_MINT, 0, 0)
        for r in rows:
            eng.on_trade(dict(r))
            stock.on_trade(dict(r))
        eng.close_all("t")
        stock.close_all("t")
        tr = trigs(out)
        self.assertGreaterEqual(len(trigs(out_s)), 10)
        self.assertEqual([json.dumps({k: v for k, v in r.items() if k != "lane_heal"}) for r in tr], [json.dumps(r) for r in trigs(out_s)])
        for r in tr:
            lh = r["lane_heal"]
            self.assertEqual((lh["status"], lh["replay"]["match"], lh["replay"]["spent_diff_lamports"], lh["replay"]["n_tip_only"]), ("not_needed", True, 0, 0), r["pool"])
        self.assertEqual({r["pool"] for r in tr}, set(pools))


class Test3GapInjection(Base):
    def heal(self, out):
        return [r for r in trigs(out) if r["variant"] == "pv"][0]["lane_heal"]

    def test_a_no_qualifying_sell_in_range_healed(self):
        scn, eng, out = self.case("a")
        h = self.heal(out)
        self.assertEqual((h["status"], h["reason"], h["kinds"], h["replay"]["match"]), ("healed", None, ["slot_jump"], True))
        self.assertEqual(h["follower"], {"done_before": 1050, "done_after": 1170, "counters_equal": True})
        self.assertEqual(h["tip_only_total_n"], 3)
        self.assertEqual(h["tip_only_in_gaps_n"], 3)
        self.assertEqual(h["boost_buys_tip_only_n"], 1)
        self.assertEqual(h["max_interval_slots"], 65)
        self.assertEqual(h["min_slots_gap_end_to_trigger"], 40)
        self.assertEqual(h["healed_through_seq"], 1)
        t = trigs(out)[0]
        self.assertGreaterEqual(t["base_breaks_unresolved_settled"], 1)  # the websocket view alone is broken ...
        self.assertEqual(t[hs.UNION_FIELD], 0)  # ... the union is whole
        self.assertTrue(t["gap"])

    def test_b_first_qualifying_sell_in_range_is_replay_earlier(self):
        _, _, out = self.case("b")
        h = self.heal(out)
        self.assertEqual((h["status"], h["reason"], h["replay"]["match"]), ("refused", "heal_replay_earlier", False))

    def test_c_boost_buys_crossing_budget_is_replay_none(self):
        _, _, out = self.case("c")
        h = self.heal(out)
        self.assertEqual((h["status"], h["reason"], h["replay"]["match"]), ("refused", "heal_replay_none", False))

    def test_d_hidden_base_chain_print_union_zero_healed_break(self):
        _, _, out = self.case("d")
        t = trigs(out)[0]
        h = t["lane_heal"]
        self.assertEqual((h["status"], h["reason"]), ("healed", None))
        self.assertGreaterEqual(t["base_breaks_unresolved_settled"], 1)  # heal_kind break: the stock executor refuses bad_intent here
        self.assertEqual(t[hs.UNION_FIELD], 0)

    def test_e_follower_counter_moved_inside(self):
        _, _, out = self.case("a", after=(1170, 1))
        h = self.heal(out)
        self.assertEqual((h["status"], h["reason"]), ("refused", "heal_follower_gap"))

    def test_e2_follower_pins_and_no_sample(self):
        write_status(self.sp, 1050, pinned=False)
        _, _, out = self.case("a", keep=True)
        self.assertEqual(self.heal(out)["reason"], "heal_follower_pins")
        write_status(self.sp, 1050)
        _, _, out = self.case("a", after=(1100, 0))  # the follower never reached R + 4
        self.assertEqual(self.heal(out)["reason"], "heal_no_sample")
        os.remove(self.sp)
        scn = base("a")
        eng, out2 = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        feed(eng, scn, None)
        self.assertEqual(self.heal(out2)["reason"], "heal_no_sample")

    def test_f_tip_high_water_behind(self):
        _, _, out = self.case("a", tip=FakeTip(hw_slot=1163))  # R = 1160, R + 4 = 1164
        self.assertEqual(self.heal(out)["reason"], "heal_tip_behind")
        _, _, out = self.case("a", tip=FakeTip(hw_slot=1164))
        self.assertEqual(self.heal(out)["status"], "healed")

    def test_f2_reader_health(self):
        write_status(self.sp, 1050)
        scn = base("a")
        tip = FakeTip()
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, tip)
        eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
        rows = scn.ws_rows()
        for r in rows[:-1]:
            eng.on_trade(dict(r))
        tip.c["resets"] += 1  # a tip reader reset after the first follower sample
        write_status(self.sp, 1170)
        for r in scn.tip_rows():
            eng.on_tip_row(r)
        eng.on_trade(dict(rows[-1]))
        self.assertEqual(self.heal(out)["reason"], "heal_reader_health")

    def test_g_ws_only_and_tip_only_in_one_slot(self):
        _, _, out = self.case("g")
        t = trigs(out)[0]
        h = t["lane_heal"]
        self.assertEqual(t[hs.UNION_FIELD], 0)
        self.assertEqual((h["status"], h["reason"]), ("refused", "heal_order_ambiguous"))
        self.assertEqual(h["ws_only_total_n"], 1)

    def test_h_integrity_pre_unlinked_and_gaps_past_index_5(self):
        # integrity entry (announced_before_gap)
        scn = base("a")
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        rows = scn.ws_rows()
        eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
        for r in rows[:-1]:
            eng.on_trade(dict(r))
        eng.pools[scn.pool].gaps.append({"kind": "announced_before_gap"})
        write_status(self.sp, 1170)
        for r in scn.tip_rows():
            eng.on_tip_row(r)
        eng.on_trade(dict(rows[-1]))
        h = self.heal(out)
        self.assertEqual((h["status"], h["reason"]), ("refused", "heal_integrity"))
        # 6+ gaps with the integrity entry past index 5: the record shows 5, the heal reads all of them
        write_status(self.sp, 1050)
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
        for r in rows[:-1]:
            eng.on_trade(dict(r))
        p = eng.pools[scn.pool]
        p.gaps.extend({"kind": "slot_jump", "from_slot": 1, "to_slot": 2} for _ in range(5))
        p.gaps.append({"kind": "ambiguous_mint"})
        self.assertGreaterEqual(len(p.gaps), 7)
        write_status(self.sp, 1170)
        for r in scn.tip_rows():
            eng.on_tip_row(r)
        eng.on_trade(dict(rows[-1]))
        t = trigs(out)[0]
        self.assertEqual(len(t["gaps"]), 5)
        self.assertTrue(all(g["kind"] != "ambiguous_mint" for g in t["gaps"]))
        self.assertEqual((t["lane_heal"]["status"], t["lane_heal"]["reason"], t["lane_heal"]["n_gaps_full"]), ("refused", "heal_integrity", len(p.gaps)))
        # pre_unlinked: the print directly before the trigger is missing from the websocket (the tip has it)
        write_status(self.sp, 1050)
        s2 = Scn()
        s2.row(1000, "buy", "A", SOL // 10)
        for sl in (1020, 1045, 1070):
            s2.row(sl, "buy", s2.pda, SOL // 2)
        s2.row(1160, "buy", "D", SOL // 20)
        s2.row(1199, "buy", "P", SOL // 20, hide=True)
        s2.row(1200, "sell", "DUMPER", int(s2.t.q + s2.t.v - 35 * SOL))
        s2.finish()
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        feed(eng, s2, self.sp)
        t = trigs(out)[0]
        self.assertEqual((t["base_breaks_unresolved"], t["base_breaks_unresolved_settled"], t[hs.UNION_FIELD]), (1, 0, 0))
        self.assertEqual((t["lane_heal"]["status"], t["lane_heal"]["reason"]), ("refused", "heal_pre_unlinked"))

    def test_union_nonzero_refused(self):
        _, _, out = self.case("a", tip_rows=[])  # the tip never delivers the hidden rows
        h = self.heal(out)
        self.assertEqual((h["status"], h["reason"]), ("refused", "heal_union_nonzero"))

    def test_rows_cap(self):
        with mock.patch.object(hl, "ROWS_CAP", 3):
            _, _, out = self.case("a")
        self.assertEqual(self.heal(out)["reason"], "heal_rows_cap")

    def test_v_missing(self):
        scn = base("a")
        tips = scn.tip_rows()
        for r in tips:
            if r["slot"] in (1100, 1110):
                r["virtual_quote_reserves"] = None
        _, _, out = self.case("a", tip_rows=tips)
        self.assertEqual(self.heal(out)["reason"], "heal_v_missing")

    def test_silence_and_restart_intervals_close_at_next_event(self):
        scn = base("a")
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
        rows = scn.ws_rows()
        for r in rows[:5]:
            eng.on_trade(dict(r))
        st = eng._lh_pools[scn.pool]
        eng.tick(eng.last_event_ms + 25_000)  # silence: L = hw_slot of the last event, R pending
        i_sil = len(eng.pools[scn.pool].gaps) - 1
        self.assertEqual(st.iv[i_sil][:2], [1095, None])
        eng.on_trade(dict(rows[5]))  # first event after it (slot 1160)
        self.assertEqual(st.iv[i_sil][:2], [1095, 1160])
        eng.feed_restart(eng.last_event_ms + 1_000, "t")
        i_rs = len(eng.pools[scn.pool].gaps) - 1
        self.assertEqual(st.iv[i_rs][:2], [1160, None])
        eng.on_trade(dict(rows[6]))
        self.assertEqual(st.iv[i_rs][:2], [1160, 1180])
        self.assertEqual([st.iv[i][2] for i in sorted(st.iv)], sorted(st.iv[i][2] for i in st.iv))

    def test_status_sampling_at_most_once_a_second_and_fail_closed(self):
        eng, _ = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        write_status(self.sp, 1000)
        for ms in (10_000, 10_400, 10_999):
            eng._lh_sample(ms)
        self.assertEqual(len(eng._lh_fol), 1)
        eng._lh_sample(11_000)
        self.assertEqual(len(eng._lh_fol), 2)
        n = 2
        for bad in ('{"last_done_slot": 5', "garbage", "", '{"gaps": 0}', '{"last_done_slot": "x"}', '{"last_done_slot": true}', "[]"):
            with open(self.sp, "w") as f:
                f.write(bad)
            eng._lh_sample(eng._lh_fol_ms + 1_000)  # a failure still counts as the second's attempt: no hot retry
            self.assertEqual(len(eng._lh_fol), n, bad)
            eng._lh_sample(eng._lh_fol_ms + 10)
            self.assertEqual(len(eng._lh_fol), n, bad)
        os.remove(self.sp)
        eng._lh_sample(eng._lh_fol_ms + 1_000)
        self.assertEqual(len(eng._lh_fol), n)
        with open(self.sp, "w") as f:  # a run whose status file is garbage never heals
            f.write("garbage")
        _, _, out = self.case("a", keep=True)
        self.assertEqual(self.heal(out)["reason"], "heal_no_sample")

    def test_tip_pending_is_tip_behind(self):
        tip = FakeTip()
        tip.is_pending = True
        _, _, out = self.case("a", tip=tip)
        self.assertEqual(self.heal(out)["reason"], "heal_tip_behind")

    def test_reader_row_loss_counters_refuse(self):
        for key in hl.HEALTH_KEYS:
            with self.subTest(key=key):
                write_status(self.sp, 1050)
                scn = base("a")
                tip = FakeTip()
                eng, out = mk(hl.build_engine_class(self.sp), self.sp, tip)
                eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
                rows = scn.ws_rows()
                for r in rows[:-1]:
                    eng.on_trade(dict(r))
                tip.c[key] += 1
                write_status(self.sp, 1170)
                for r in scn.tip_rows():
                    eng.on_tip_row(r)
                eng.on_trade(dict(rows[-1]))
                self.assertEqual(self.heal(out)["reason"], "heal_reader_health")
        for key in ("fragments_dropped", "long_lines_dropped", "parse_errors", "no_dir_polls"):
            self.assertIn(key, hl.HEALTH_KEYS)

    def test_pending_buffer_overflow_is_rows_cap(self):
        write_status(self.sp, 1050)
        scn = base("a")
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
        with mock.patch.object(hl, "TIP_PENDING_ROWS", 2):
            for r in scn.tip_rows()[:4]:  # tip rows of an announced pool the feed has not judged yet
                eng.on_tip_row(r)
        self.assertIn(scn.pool, eng._lh_pending_over)
        rows = scn.ws_rows()
        for r in rows[:-1]:
            eng.on_trade(dict(r))
        write_status(self.sp, 1170)
        for r in scn.tip_rows():
            eng.on_tip_row(r)
        eng.on_trade(dict(rows[-1]))
        self.assertEqual(self.heal(out)["reason"], "heal_rows_cap")

    def test_boost_list_overflow_sets_cap(self):
        scn = base("a")
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
        for r in scn.ws_rows()[:3]:
            eng.on_trade(dict(r))
        st = eng._lh_pools[scn.pool]
        with mock.patch.object(hl, "ROWS_CAP", 2):
            for _ in range(3):
                eng.on_boost_event({"pool": scn.pool, "authority": "x", "boost_vault_remaining": 1}, 1050, 0)
        self.assertTrue(st.cap_ws)

    def test_i_tip_only_row_below_live_s0_is_replay_diff(self):
        scn = base("a")
        first = dict(scn.rows[0])
        pre = dict(first, slot=990, side="buy", trader="ZZ", token_raw=1000, base_reserve=first["base_reserve"] + 1000, signature="below-s0", event_index=0, tx_index=0,
                   sol_lamports=1000, event_ts=first["event_ts"] - 4)
        _, _, out = self.case("a", tip_rows=scn.tip_rows() + [pre])
        t = trigs(out)[0]
        self.assertEqual(t[hs.UNION_FIELD], 0)  # the union is whole: only the replay can tell
        self.assertEqual((t["lane_heal"]["status"], t["lane_heal"]["reason"]), ("refused", "heal_replay_diff"))

    def test_store_error_refuses_no_store(self):
        scn = base("a")
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        eng.on_create_pool(scn.pool, MINT, hs.WSOL_MINT, 999, 0)
        rows = scn.ws_rows()
        for r in rows[:-1]:
            eng.on_trade(dict(r))
        eng._lh_pools[scn.pool].err = True
        eng.on_trade(dict(rows[-1]))
        self.assertEqual(self.heal(out)["reason"], "heal_no_store")

    def test_live_sps_path(self):
        rows, pools = synthetic_hour(3)
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip(hw_slot=10**9), live_sps=True)
        stock, out_s = mk(STOCK, tip=FakeTip(hw_slot=10**9), live_sps=True)
        for p in pools:
            for e in (eng, stock):
                e.on_create_pool(p, "Mint" + p[4:], hs.WSOL_MINT, 0, 0)
        for r in rows:
            eng.on_trade(dict(r))
            stock.on_trade(dict(r))
        self.assertTrue(trigs(out_s))
        self.assertEqual([json.dumps({k: v for k, v in r.items() if k != "lane_heal"}) for r in trigs(out)], [json.dumps(r) for r in trigs(out_s)])
        self.assertTrue(all(r["lane_heal"]["replay"]["match"] is True for r in trigs(out)))

    def test_store_freed_at_pool_close(self):
        scn, eng, out = self.case("a")
        self.assertIn(scn.pool, eng._lh_pools)
        eng.close_all("t")
        self.assertEqual(eng._lh_pools, {})

    def test_replay_error_is_refused_not_raised(self):
        scn = base("a")
        eng, out = mk(hl.build_engine_class(self.sp), self.sp, FakeTip())
        with mock.patch.object(hl, "_PINNED_ENGINE", side_effect=RuntimeError("boom")):
            feed(eng, scn, self.sp)
        self.assertEqual(self.heal(out)["reason"], "heal_replay_error")
        self.assertTrue(trigs(out))


if __name__ == "__main__":
    unittest.main()
