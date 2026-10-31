"""Tests for tools/hv6_cards.py (HV6 GEN scanner over unmodified hv4_cards / hv4_chain). SYNTHETIC fixtures only: no tape, no HV4/Q50 output, no network, no model.
Run: /data/mal/venv/bin/python -m unittest tools.test_hv6_cards"""
from __future__ import annotations

import hashlib
import inspect
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tools import hv4_cards as hc
from tools import hv6_cards as h6
from tools.test_hv4_cards import POOL, T, base_chain, base_prints, base_snap

REPO = Path(__file__).resolve().parent.parent
MIN = 60_000
T_BOARD = (T // (15 * MIN)) * (15 * MIN) + 2_000  # a 15-minute mark at :02


def blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def feats(**over):
    f = {"price": 2e-7, "ret_60m": 0.12, "ret_15m": -0.05, "net_flow_60m": 50.0, "vol_15m": 5.0, "vol_60m": 20.0, "age_s": 4 * 3600.0,
         "hi_6h": 3e-7, "lo_6h": 1.5e-7, "avg_vol_15m_6h": 4.0}
    f.update(over)
    return f


def pool_snap(i: int, **over):
    pid = f"P{i:02d}" + "A" * 40
    return base_snap(id=pid, pool=pid, mint=f"M{i:02d}" + "M" * 40, **over)


class BlobSha(unittest.TestCase):
    """The claim 'universe unmodified': HV4's two files are byte-identical to HV4's frozen sha 9ed4c5ff."""

    def test_pins_equal_working_tree(self):
        for rel, want in h6.HV4_BLOB_SHAS.items():
            self.assertEqual(blob_sha(REPO / rel), want, rel)

    def test_pins_start_with_the_blobs_the_design_names(self):
        self.assertTrue(h6.HV4_BLOB_SHAS["tools/hv4_cards.py"].startswith("747fd996fcb8"))
        self.assertTrue(h6.HV4_BLOB_SHAS["tools/hv4_chain.py"].startswith("95556d436069"))

    def test_pins_equal_frozen_commit_when_available(self):
        for rel, want in h6.HV4_BLOB_SHAS.items():
            r = subprocess.run(["git", "-C", str(REPO), "rev-parse", f"{h6.HV4_FROZEN_SHA}:{rel}"], capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest("frozen commit not in this clone")
            self.assertEqual(r.stdout.strip(), want, rel)

    def test_hv6_imports_the_same_module_objects(self):
        import tools.hv4_chain as chain
        self.assertIs(h6.C, hc)
        self.assertIs(h6.CHAIN, chain)


class AgeGuard(unittest.TestCase):
    def test_under_two_hours_refused(self):
        s = base_snap(age_s=1.9 * 3600, age_since_first_seen_s=1.9 * 3600, created_ms=T - int(1.9 * 3600 * 1000))
        with self.assertRaises(hc.CardRefused) as cm:
            h6.age_guard(s)
        self.assertEqual(cm.exception.reasons, ["age_under_2h"])

    def test_exactly_two_hours_ok_and_older_ok(self):
        h6.age_guard(base_snap(age_s=2 * 3600.0, age_since_first_seen_s=2 * 3600.0))
        h6.age_guard(base_snap())

    def test_unknown_age_refused(self):
        s = base_snap()
        for k in ("age_s", "age_since_first_seen_s", "created_ms", "created_known"):
            s.pop(k, None)
        with self.assertRaises(hc.CardRefused):
            h6.age_guard(s)

    def test_build_card_guard_runs_before_hv4(self):
        s = base_snap(age_s=3600.0, age_since_first_seen_s=3600.0, created_ms=T - 3600_000)
        with self.assertRaises(hc.CardRefused) as cm:
            h6.build_card(s, base_prints(), base_chain(), as_of_ms=T, as_of_slot=1000)
        self.assertEqual(cm.exception.reasons, ["age_under_2h"])

    def test_build_card_equals_hv4_card_bytes(self):
        a = h6.build_card(base_snap(), base_prints(), base_chain(), as_of_ms=T, as_of_slot=1000)
        b = hc.build_card(base_snap(), base_prints(), base_chain(), as_of_ms=T, as_of_slot=1000)
        self.assertEqual(hc.card_json(a), hc.card_json(b))
        self.assertEqual(hc.card_sha256(a), hc.card_sha256(b))

    def test_scan_counts_age_guard_even_if_filters_were_to_pass(self):
        young = base_snap(age_s=3600.0, age_since_first_seen_s=3600.0)
        ok = hc.FilterResult(True, (), {}, "young", True, 0.25)
        with mock.patch.object(h6.C, "filters", return_value=ok):
            r = h6.scan([(young, base_prints())], as_of_ms=T, limiter=hc.SetupLimiter())
        self.assertEqual((r.counts["age_guard"], r.candidates), (1, []))


class GenSetups(unittest.TestCase):
    def test_pullback_fires(self):
        t = h6.gen_setup(feats(), pool_id=POOL, as_of_ms=T)
        self.assertEqual((t.type, t.persona), ("PULLBACK", "CON"))

    def test_one_tag_when_con_and_big_both_fire(self):
        f = feats(price=3e-7, hi_6h=3e-7, lo_6h=1e-7, ret_15m=-0.05, vol_15m=20.0, avg_vol_15m_6h=4.0, age_s=7 * 3600.0)  # PULLBACK (CON) and BREAKOUT (BIG)
        self.assertGreaterEqual(len(hc.setups(f, pool_id=POOL, as_of_ms=T)), 2)  # HV4 would return one per persona
        self.assertEqual(h6.gen_setup(f, pool_id=POOL, as_of_ms=T).type, "PULLBACK")

    def test_big_only_picks_breakout_before_second_wave(self):
        f = feats(ret_15m=0.15, ret_60m=0.0, price=3e-7, hi_6h=3e-7, lo_6h=1e-7, vol_15m=20.0, age_s=7 * 3600.0)  # BREAKOUT and SECOND_WAVE shapes
        self.assertEqual([t.type for t in hc.setups(f, pool_id=POOL, as_of_ms=T)], ["BREAKOUT"])  # HV4 itself prefers BREAKOUT within BIG
        self.assertEqual(h6.gen_setup(f, pool_id=POOL, as_of_ms=T).type, "BREAKOUT")
        f2 = dict(f, vol_15m=5.0)  # no volume surge: SECOND_WAVE only
        self.assertEqual(h6.gen_setup(f2, pool_id=POOL, as_of_ms=T).type, "SECOND_WAVE")

    def test_no_setup_on_flat(self):
        self.assertIsNone(h6.gen_setup(feats(ret_60m=0.0, ret_15m=0.0), pool_id=POOL, as_of_ms=T))

    def test_null_input_fails_closed(self):
        self.assertIsNone(h6.gen_setup(feats(ret_15m=None), pool_id=POOL, as_of_ms=T))

    def test_cooldown_is_per_pool_30_min_keyed_gen(self):
        lim = hc.SetupLimiter()
        lim.record(POOL, h6.PERSONA, T)
        self.assertIsNone(h6.gen_setup(feats(), pool_id=POOL, as_of_ms=T + 29 * MIN, limiter=lim))
        self.assertIsNotNone(h6.gen_setup(feats(), pool_id=POOL, as_of_ms=T + 30 * MIN, limiter=lim))
        self.assertIsNotNone(h6.gen_setup(feats(), pool_id="Q" * 44, as_of_ms=T + MIN, limiter=lim))

    def test_hv4_persona_cooldown_does_not_block_gen(self):
        lim = hc.SetupLimiter()
        lim.record(POOL, "CON", T)
        self.assertIsNotNone(h6.gen_setup(feats(), pool_id=POOL, as_of_ms=T + MIN, limiter=lim))

    def test_gen_does_not_mutate_limiter(self):
        lim = hc.SetupLimiter()
        h6.gen_setup(feats(), pool_id=POOL, as_of_ms=T, limiter=lim)
        self.assertTrue(lim.allowed(POOL, h6.PERSONA, T))

    def test_tag_builds_a_card(self):
        t = h6.gen_setup(feats(), pool_id=POOL, as_of_ms=T)
        c = h6.build_card(base_snap(), base_prints(), base_chain(), as_of_ms=T, as_of_slot=1000, setup=t)
        self.assertEqual(c["setup"]["type"], "PULLBACK")


class ScanBoard(unittest.TestCase):
    def run_scan(self, snaps, t=T, per_pool=None, **kw):
        lim = kw.pop("limiter", hc.SetupLimiter())
        per_pool = per_pool or {}

        def fake_features(snap, prints, *, as_of_ms, series=None):
            return per_pool.get(snap["id"], feats())

        with mock.patch.object(h6.C, "features", side_effect=fake_features):
            return h6.scan([(s, base_prints()) for s in snaps], as_of_ms=t, limiter=lim, **kw), lim

    def test_board_second(self):
        self.assertTrue(h6.is_board_second(T_BOARD))
        self.assertFalse(h6.is_board_second(T_BOARD + MIN))
        self.assertFalse(h6.is_board_second(T_BOARD + 1000))

    def test_board_is_top_4_untagged_by_volume_ties_by_id(self):
        snaps = [pool_snap(i) for i in range(30)]
        pf = {s["id"]: feats(vol_60m=float(i), ret_60m=0.0, ret_15m=0.0) for i, s in enumerate(snaps)}
        pf[snaps[29]["id"]] = feats(vol_60m=29.0)  # pullback: tagged, so off the board
        r, _ = self.run_scan(snaps, t=T_BOARD, per_pool=pf)
        self.assertEqual(len(r.board), 4)  # fix B: one call per board (was 24)
        self.assertNotIn(snaps[29]["id"], r.board)
        self.assertEqual(r.board, [snaps[i]["id"] for i in (28, 27, 26, 25)])

    def test_no_board_off_the_mark(self):
        r, _ = self.run_scan([pool_snap(i) for i in range(3)], t=T_BOARD + MIN)
        self.assertEqual(r.board, [])

    def test_board_off_switch(self):
        r, _ = self.run_scan([pool_snap(1)], t=T_BOARD, board_on=False)
        self.assertEqual(r.board, [])

    def test_tags_top_k_and_cooldown_recorded_only_for_passed_on(self):
        snaps = [pool_snap(i) for i in range(12)]
        pf = {s["id"]: feats(vol_15m=float(i + 1)) for i, s in enumerate(snaps)}
        r, lim = self.run_scan(snaps, per_pool=pf)
        self.assertEqual((len(r.ranked), len(r.tags)), (12, h6.TOP_K_PER_SCAN))
        passed = {t.pool_id for t in r.tags}
        for s in snaps:
            self.assertEqual(lim.allowed(s["id"], h6.PERSONA, T), s["id"] not in passed)

    def test_second_scan_inside_cooldown_gives_no_repeat(self):
        s = [pool_snap(1)]
        r1, lim = self.run_scan(s)
        later = [pool_snap(1, now_ms=T + MIN - 500, last_ms=T + MIN - 5000)]
        r2, _ = self.run_scan(later, t=T + MIN, limiter=lim)
        self.assertEqual((len(r1.tags), len(r2.tags), r2.counts["cooldown_blocked"]), (1, 0, 1))

    def test_x_takes_top_ranked_within_cap(self):
        snaps = [pool_snap(i) for i in range(5)]
        pf = {s["id"]: feats(vol_15m=float(i + 1)) for i, s in enumerate(snaps)}
        cap = h6.XCap()
        r, _ = self.run_scan(snaps, per_pool=pf, x_cap=cap)
        self.assertEqual([t.pool_id for t in r.x_tags], [t.pool_id for t in r.tags[:2]])
        cap.record(T - 10 * MIN)
        r, _ = self.run_scan(snaps, per_pool=pf, x_cap=cap)
        self.assertEqual(len(r.x_tags), 1)

    def test_x_cap_window_is_sliding_hour(self):
        c = h6.XCap()
        c.record(T)
        c.record(T + MIN)
        self.assertEqual(c.remaining(T + 59 * MIN), 0)
        self.assertEqual(c.remaining(T + 60 * MIN), 1)  # t > now - 60 min: the first entry has just aged out
        self.assertEqual(c.remaining(T + 61 * MIN), 2)

    def test_filter_fail_and_leak_counted_and_skipped(self):
        bad = pool_snap(1, quote_wsol=False)
        leak = pool_snap(2, last_ms=T + 5)
        r, _ = self.run_scan([bad, leak, pool_snap(3)])
        self.assertEqual((r.counts["filter_fail"], r.counts["leaks"], len(r.candidates)), (1, 1, 1))

    def test_scan_prints_nothing_and_counts_only(self):
        r, _ = self.run_scan([pool_snap(1)])
        self.assertTrue(all(isinstance(v, int) for v in r.counts.values()))


class Limits(unittest.TestCase):
    def test_design_table_values(self):
        L = h6.GEN_CODE_LIMITS
        self.assertEqual((L["min_conviction"], L["hard_stop_pct"], L["max_hold_min"], L["entries_per_h"], L["max_open"], L["day_loss_stop_sol"]),
                         (3, -40.0, 720, 3, 6, 1.5))
        self.assertEqual((h6.BOARD_SIZE, h6.BOARD_EVERY_MS, h6.SCAN_SECOND_MS, h6.CARDS_PER_CALL, h6.ENTRY_CALLS_PER_H), (4, 900_000, 2000, 4, 12))
        # fix B: the board is one call per board scan, 4 calls/h per arm, inside the 12/h entry-call cap with two thirds left to setups
        self.assertEqual((h6.BOARD_CALLS_PER_H, h6.GEN_CODE_LIMITS["board"]), (4, 4))
        self.assertLessEqual(h6.BOARD_CALLS_PER_H, h6.ENTRY_CALLS_PER_H // 3)
        self.assertEqual((h6.X_EXIT["stop_pct"], h6.X_EXIT["target_pct"], h6.X_EXIT["time_min"], h6.X_CAP_PER_H), (-8.0, 20.0, 60, 2))
        self.assertEqual(h6.GEN_KNOB_BOUNDS["time_min"], (10, 360))

    def test_universe_constants_are_hv4s(self):
        self.assertEqual((hc.AGE_MIN_S, hc.AGE_MAX_S, hc.QUOTE_FLOOR_SOL, hc.IMPACT_RT_CAP), (7200, 7 * 86400, 30.0, 0.03))
        self.assertEqual(h6.MIN_AGE_S, 7200)


class PathGuard(unittest.TestCase):
    OK_READ = ["/data/mal/arena/tape/2026-10-20/h00.jsonl", "/data/mal/change-radar-arena1/radar.jsonl", "/data/mal/arena/haiku-v6/engine-ledger.jsonl",
               "/data/mal/arena/haiku-v6/decision-log.jsonl", "/data/mal/arena/haiku-v6/score/read-v1.json", "/data/mal/arena/haiku-v6/regime/radar-copy.jsonl",
               "/data/mal/arena/haiku-v6/chain-cache/x.json", "/data/mal/arena/haiku-v6/spend-ledger.jsonl"]

    def test_reads_allowed(self):
        for p in self.OK_READ:
            self.assertEqual(h6.check(p, "r"), p)

    def test_writes_only_hv6_root(self):
        for p in self.OK_READ[2:]:
            h6.check(p, "w")
        for p in [self.OK_READ[0], self.OK_READ[1], "/data/mal/arena/other.json", "/tmp/x", "/data/mal/arena/haiku-v6x/a"]:
            with self.assertRaises(h6.PathRefused, msg=p):
                h6.check(p, "w")

    def test_refuses_other_books_and_sealed(self):
        bad = ["/data/mal/arena/bots.json", "/data/mal/arena/haiku-v4/ledger.jsonl", "/data/mal/arena/ledger/hv4.0-CON-L.jsonl",
               "/data/mal/arena/q50/decisions.jsonl", "/data/mal/arena/score.json", "/data/mal/arena/decision-log.jsonl",
               "/data/mal/arena/haiku-v6/../haiku-v4/ledger.jsonl", "/data/mal/arena/haiku-v6/bots.json",
               "/data/mal/arena/haiku-v6/shadow/x", "/data/mal/arena/haiku-v6/canary-x", "/data/mal/arena/tape/forward-1016/h00",
               "/data/mal/arena/tape/truth-x", "/data/mal/arena/tape/fresh-08-14", "/data/mal/arena/tape/exp011-a", "/data/mal/arena/tape/tip-tape-archive/a",
               "/data/mal/arena/tape/meta-capture/a", "/data/mal/arena/tape/blocks/forward-1016/a", "/var/lib/mal/sealed/x",
               "/data/mal/arena/haiku-v6/hv4/x", "/data/mal/arena/haiku-v6/Q50.json", "relative/path", "", "/data/mal/arena/tape/../haiku-v4/x"]
        for p in bad:
            for m in ("r", "w"):
                with self.assertRaises(h6.PathRefused, msg=f"{p} {m}"):
                    h6.check(p, m)

    def test_error_message_does_not_echo_path(self):
        try:
            h6.check("/data/mal/arena/bots.json")
        except h6.PathRefused as e:
            self.assertNotIn("bots", str(e))

    def test_symlink_out_of_allowlist_refused(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            link = os.path.join(d, "l")
            os.symlink("/etc", link)
            with self.assertRaises(h6.PathRefused):
                h6.check(link)

    def test_guarded_open_refuses_before_opening(self):
        with self.assertRaises(h6.PathRefused):
            h6.guarded_open("/data/mal/arena/bots.json")
        with self.assertRaises(h6.PathRefused):
            h6.guarded_open("/data/mal/arena/tape/x", "w")

    def test_hv6_real_file_names_are_not_blocked(self):
        for n in ("ledger", "decision", "score", "playbook", "knobs", "regime", "manifest", "spend", "release", "review", "read"):
            h6.check(f"/data/mal/arena/haiku-v6/{n}-v1.jsonl", "w")
        for p in self.OK_READ:
            self.assertNotIn("probe", p)


class ImportGuard(unittest.TestCase):
    def test_hv6_cards_imports_only_hv4_cards_and_chain(self):
        src = inspect.getsource(h6)
        self.assertEqual(h6.forbidden_imports(src), [])

    def test_detects_output_reading_modules(self):
        for line in ("import tools.hv4_read", "from tools import hv4_runner", "from tools.hv4_shakedown_report import x", "from tools import hv4_p0_count as p",
                     "import hv4_review"):
            self.assertTrue(h6.forbidden_imports(line), line)

    def test_allows_cards_and_chain(self):
        self.assertEqual(h6.forbidden_imports("from tools import hv4_cards, hv4_chain\nimport tools.hv4_cards"), [])

    def test_hv6_source_never_opens_files_or_network(self):
        src = inspect.getsource(h6)
        for tok in ("socket", "urllib", "requests", "subprocess", "bots.json\")", "os.environ"):
            self.assertNotIn(tok, src.replace("bots.json\", \"shadow", ""))

    def test_hv4_files_not_in_diff_of_this_branch(self):
        r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", "origin/main", "--", "tools/hv4_cards.py", "tools/hv4_chain.py"], capture_output=True, text=True)
        if r.returncode == 0:
            self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()


def test_import_guard_refuses_arena_readers():
    from tools import hv6_cards as H6

    src = "from tools import arena_score\nimport tools.arena_am6_read\nfrom tools.arena_report_legs import x\nfrom tools import arena_worst_gaps, arena_paper, arena_declog, arena_state\n"
    assert sorted(H6.forbidden_imports(src)) == ["arena_am6_read", "arena_report_legs", "arena_score", "arena_worst_gaps"]
