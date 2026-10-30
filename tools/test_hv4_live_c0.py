"""Offline tests for tools/hv4_live_c0.py and its wiring in tools/arena_live_bridge.py. Synthetic fixtures only: no HV4 ledger, decision row, bots.json
or job log is read, no network, no key."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_arms
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import Env, StubPort, TmpCase, T0

ARM = "hv4-CON-X"
FUNDED = 600_000_000


def raw(**over):
    return {"profile": c0.PROFILE, "funded_lamports": FUNDED, **over}


def read_record(bot, **over):
    """A synthetic recorded read (the keys arena_score.record_read writes that the gate reads). No real record is ever opened."""
    rec = {"bot_id": bot, "status": "PASS", "read_boundary_ms": 1_700_000_000_000, "attempts": 120, "model_ids": [c0.MODEL_ID] if bot.endswith("-F") else [],
           "am15": {"rule": "synthetic"}, "frozen_sha": c0.FROZEN_SHA}
    rec.update(over)
    return rec


def write_read_record(reads_dir, bot, **over):
    d = Path(reads_dir)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o755)
    f = d / f"{bot}.json"
    f.write_text(json.dumps(read_record(bot, **over), sort_keys=True) + "\n")
    os.chmod(f, 0o644)


def owner_fixture(tmp, bot):
    """A synthetic stand-in for /etc/mal-hv4-c0/<bot_id>/CANARY_DEC.md, under <tmp>/etc-c0 (what live_main_env points OWNER_DEC_DIR at). Returns its path."""
    d = Path(tmp) / "etc-c0" / bot
    d.mkdir(parents=True, exist_ok=True)
    p = d / c0.OWNER_DEC_NAME
    p.write_text("synthetic owner decision\n" + c0.OWNER_LINE_FMT.format(bot_id=bot) + "\n")
    return p


def live_main_env(tmp, bot):
    """(extra config keys, context manager) so a LIVE main() reaches the later gates: the three parity paths and the owner file (synthetic, patched in).
    The patches start only when the context manager is entered."""
    import contextlib
    from unittest import mock
    owner_fixture(tmp, bot)
    paths = {"parity_dir": str(Path(tmp) / "par"), "twins_file": str(Path(tmp) / "twins.jsonl"), "tieout_dir": str(Path(tmp) / "tie")}

    @contextlib.contextmanager
    def patches():
        with mock.patch.object(c0, "OWNER_DEC_DIR", str(Path(tmp) / "etc-c0")), mock.patch.object(alb, "root_ok_problem", lambda _p: None):
            yield

    return paths, patches()


class Hv4Env(Env):
    """Env whose bot_id is an HV4 F/X arm and whose config carries the C0 profile."""

    def __init__(self, tmp, live=False, bot=ARM, **over):
        reads = os.path.realpath(str(Path(tmp) / "reads"))
        win = os.path.realpath(str(Path(tmp) / "window"))
        os.makedirs(win, exist_ok=True)
        os.chmod(win, 0o755)
        c0.READS_DIR_CANON, c0.WINDOW_DIR = reads, win  # test-only: the latest env's dirs are the "canonical" ones (module globals, reset by the next env)
        self.a3_file = str(Path(tmp) / "a3" / "daily.jsonl")
        super().__init__(tmp, live=live, **{"bot_id": bot, "frozen_sha": c0.FROZEN_SHA, "profile": c0.PROFILE, "funded_lamports": FUNDED, "reads_dir": reads,
                                            "a3_record_file": self.a3_file, **over})
        write_read_record(reads, bot)
        self.bot = bot
        self.declog_bot = bot
        self.refresh_halt_inputs()

    def refresh_halt_inputs(self):
        """A fresh, connected feed status and a clean A3 record at the test clock (the halts tests overwrite them on purpose)."""
        write_feed(self.raw["root"], self.bot, self.clock.t)
        write_a3(self.a3_file, self.clock.t)

    def bridge(self):
        self.refresh_halt_inputs()
        return super().bridge()

    def row(self, body, ms=None):
        write_feed(self.raw["root"], self.bot, self.clock.t)  # a fresh feed at the time of every row; halt tests overwrite it after the rows are written
        return self.declog.append(self.bot, self.clock.t - 100 if ms is None else ms, body)

    def entry_sz(self, oid, pool="P1", stake_sol=0.05, size=None):
        b = {"kind": "entry", "order_id": oid, "pool": pool, "meta": {}, "decision_slot": 1000, "stake_sol": stake_sol, "attempt_key": None}
        if size is not None:
            b["size"] = size
        return self.row(b)

    def start(self, sha=c0.FROZEN_SHA):
        self.row({"kind": "start", "binding": "b", "frozen_sha": sha, "rules": [self.bot]})


def write_feed(root, bot_id, now_ms, state="connected", tapegaps=True, **over):
    """A synthetic forwarder status file (decision_forwarder_v1 format) at <root>/transport/<bot>.status.json, plus a fresh carried tapegaps stream (see write_tapegaps)."""
    d = Path(root) / "transport"
    d.mkdir(parents=True, exist_ok=True)
    st = {"schema": c0.FEED_SCHEMA, "bot": bot_id, "state": state, "ms": now_ms, "last_frame_ms": now_ms, "last_remote_ms": now_ms, "seq": 1, "f": "x.jsonl", "o": 0,
          "rows": 1, "gaps": 0, "stall_ms": 2000, "pid": 1}
    st.update(over)
    f = d / f"{bot_id}.status.json"
    f.write_text(json.dumps(st, sort_keys=True) + "\n")
    if tapegaps:
        write_tapegaps(root, bot_id, now_ms)
    return f


def write_tapegaps(root, bot_id, now_ms, rows=None):
    """A synthetic CARRIED relay stream (<root>/transport/<bot>.tapegaps.jsonl) whose mtime is the test clock, as the forwarder's puller sets it (the source pass instant).
    rows=None keeps the rows already there (an empty file when new); a list rewrites them. Every `write_feed` calls this, so a test that expects buys has a fresh stream
    (the hv4_c0 profile refuses new buys without one, QP #969 P7-c); pass tapegaps=False to write_feed for the missing/stale cases."""
    d = Path(root) / "transport"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{bot_id}.tapegaps.jsonl"
    if rows is not None or not f.exists():
        f.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in (rows or [])))
    os.utime(f, ns=(now_ms * 1_000_000, now_ms * 1_000_000))
    return f


def write_a3(path, now_ms, halt=False, guard="NONE", **over):
    """A synthetic A3 daily record (one line; the newest line is the state). DEC-032 Am.3: also the pooled guard's applied line for the SAME run_utc
    (<dir>/a3-applied.jsonl) with verdict `guard`; guard=None writes none."""
    from datetime import datetime, timezone
    run = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {"run_utc": run, "halt": {"flags": {"pins_changed": {"halt": halt, "evaluated": True}, "boost_disabled": {"halt": False, "evaluated": True}}},
           "warn": {"flags": {"program_changed": {"warn": False}}}}
    rec.update(over)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rec) + "\n")
    if guard is not None:
        (Path(path).parent / c0.A3_APPLIED_NAME).write_text(json.dumps({"run_utc": rec["run_utc"], "verdict": guard}) + "\n")


class ConstantTests(unittest.TestCase):
    def test_table_matches_dec032_c0(self):
        self.assertEqual(c0.C0_LIMITS, {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30,
                                        "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000})
        self.assertEqual((c0.FUNDED_MIN_LAMPORTS, c0.FUNDED_MAX_LAMPORTS), (350_000_000, 1_000_000_000))
        self.assertEqual(c0.TIER_NAME, "T1")
        self.assertIsNone(c0.guard_problem())

    def test_sizes_mirror_hv4_arms(self):
        self.assertEqual(c0.HV4_SIZES, tuple(hv4_arms.SIZES))

    def test_pin_equals_executor_default(self):
        try:
            from tools import h5_executor as h5
        except ModuleNotFoundError as e:  # solders is not installed in every checkout
            self.skipTest(str(e))
        d = h5.H5_DEFAULT
        self.assertEqual((c0.PRIORITY_PIN["buy"], c0.PRIORITY_PIN["sell"], c0.PRIORITY_PIN["escalated"]),
                         (d["buy_priority_lamports"], d["sell_priority_lamports"], d["escalated_priority_lamports"]))

    def test_reserve_sums(self):
        self.assertEqual(c0.TRIP_FEE_RESERVE_LAMPORTS, 60_000 + 155_000)
        self.assertEqual(c0.SELL_FEE_RESERVE_LAMPORTS, 155_000)
        self.assertLess(c0.TRIP_FEE_RESERVE_LAMPORTS, alb.FEE_RESERVE_LAMPORTS)  # the old 500,000-priority reserve was 1,010,000

    def test_guard_trips_on_loosened_constant(self):
        old = dict(c0.C0_LIMITS)
        try:
            c0.C0_LIMITS["stake_lamports"] = 50_000_001
            self.assertEqual(c0.guard_problem(), "c0_guard:stake_lamports")
            c0.C0_LIMITS.update(old)
            c0.C0_LIMITS["total_loss_lamports"] = 200_000_001
            self.assertEqual(c0.guard_problem(), "c0_guard:total_loss_lamports")
            c0.C0_LIMITS.update(old)
            c0.C0_LIMITS["daily_loss_lamports"] = 200_000_000
            c0.C0_LIMITS["total_loss_lamports"] = 150_000_000
            self.assertIsNotNone(c0.guard_problem())
        finally:
            c0.C0_LIMITS.update(old)


class RefusalTests(unittest.TestCase):
    def test_arms(self):
        for ok in ("hv4-CON-F", "hv4-BIG-X", "hv4-CON-X"):
            self.assertIsNone(c0.arm_refusal(ok), ok)
        for name in hv4_arms.ARMS:  # every registered arm name (code constants only): refused exactly when its role is not F or X
            self.assertEqual(c0.arm_refusal("hv4-" + name) is None, name.endswith(("-F", "-X")), name)
        for bad in ("hv4-CON-L", "hv4-BIG-L", "hv4-CON-P", "hv4-BIG-R", "hv4-CON-R"):
            self.assertEqual(c0.arm_refusal(bad), "arm_not_canary_candidate", bad)
        self.assertEqual(c0.arm_refusal("bot-x"), "arm_not_hv4")
        self.assertEqual(c0.arm_refusal("hv4-CON-Q"), "arm_not_f_or_x")
        self.assertEqual(c0.arm_refusal(None), "arm_not_hv4")

    def test_funded_range_edges(self):
        self.assertIsNone(c0.funded_refusal(350_000_000))
        self.assertIsNone(c0.funded_refusal(1_000_000_000))
        self.assertEqual(c0.funded_refusal(349_999_999), "funded_lamports_out_of_range")
        self.assertEqual(c0.funded_refusal(1_000_000_001), "funded_lamports_out_of_range")
        self.assertEqual(c0.funded_refusal(None), "funded_lamports_missing")
        self.assertEqual(c0.funded_refusal(True), "funded_lamports_out_of_range")
        self.assertEqual(c0.funded_refusal(5e8), "funded_lamports_out_of_range")

    def test_floor(self):
        self.assertEqual(c0.wallet_floor_lamports(350_000_000), 100_000_000)
        self.assertEqual(c0.wallet_floor_lamports(1_000_000_000), 750_000_000)
        self.assertEqual(c0.wallet_floor_lamports(600_000_000, 500_000_000), 500_000_000)  # raise only
        self.assertEqual(c0.wallet_floor_lamports(600_000_000, 1), 350_000_000)  # never lowered
        self.assertGreaterEqual(c0.wallet_floor_lamports(300_000_000), c0.MIN_WALLET_FLOOR_LAMPORTS)

    def test_fee_pin(self):
        base = {"bot_id": "hv4-CON-F", "frozen_sha": c0.FROZEN_SHA, "funded_lamports": FUNDED}
        self.assertIsNone(c0.config_refusal(base, live=True))
        self.assertIsNone(c0.config_refusal({**base, "priority_pin": dict(c0.PRIORITY_PIN)}, live=True))
        self.assertIsNone(c0.config_refusal({**base, "buy_priority_lamports": 55_000}, live=True))
        p155 = {"profile": "p155", "buy": 155_000, "sell": 155_000, "escalated": 155_000}
        self.assertEqual(c0.config_refusal({**base, "priority_pin": p155}, live=True), "priority_pin_mismatch")
        self.assertEqual(c0.config_refusal({**base, "priority_pin": {**c0.PRIORITY_PIN, "buy": 55_001}}, live=True), "priority_pin_mismatch")
        self.assertEqual(c0.config_refusal({**base, "buy_priority_lamports": 500_000}, live=True), "buy_priority_lamports_not_pin")
        self.assertEqual(c0.config_refusal({**base, "priority_lamports": 500_000}, live=True), "priority_lamports_not_pin")
        self.assertEqual(c0.config_refusal({**base, "sell_priority_lamports": 55_000}, live=True), "sell_priority_lamports_not_configurable")
        self.assertEqual(c0.config_refusal({**base, "escalated_priority_lamports": 150_000}, live=True), "escalated_priority_lamports_not_configurable")

    def test_funded_required_when_live(self):
        self.assertEqual(c0.config_refusal({"bot_id": "hv4-CON-X", "frozen_sha": c0.FROZEN_SHA}, live=True), "funded_lamports_missing")
        self.assertIsNone(c0.config_refusal({"bot_id": "hv4-CON-X", "frozen_sha": c0.FROZEN_SHA}, live=False))

    def test_live_ok_names_bot(self):
        self.assertIsNone(c0.live_ok_problem("hv4-CON-F\n", "hv4-CON-F"))
        self.assertEqual(c0.live_ok_problem("hv4-CON-X\n", "hv4-CON-F"), "live_ok_other_book")
        self.assertEqual(c0.live_ok_problem("hv4-CON-F\nextra\n", "hv4-CON-F"), "live_ok_other_book")
        self.assertEqual(c0.live_ok_problem("", "hv4-CON-F"), "live_ok_other_book")


class StakeTests(unittest.TestCase):
    def test_size_mapping_all_sizes(self):
        for size in hv4_arms.SIZES:
            s, rec = c0.c0_stake({"stake_sol": size, "size": size})
            self.assertEqual(s, min(int(size * 1e9), 50_000_000), size)
            self.assertEqual(rec["twin_stake_lamports"], s)
        s, rec = c0.c0_stake({"stake_sol": 0.25, "size": 0.25})
        self.assertEqual(s, 50_000_000)
        self.assertTrue(rec["clamped"])

    def test_edges_at_cap_and_one_over(self):
        s, rec = c0.c0_stake({"stake_sol": 0.05})
        self.assertEqual((s, rec["clamped"]), (50_000_000, False))
        s, rec = c0.c0_stake({"stake_sol": 0.050000001})
        self.assertEqual((s, rec["clamped"]), (50_000_000, True))
        s, _ = c0.c0_stake({"stake_sol": 0.049999999})
        self.assertEqual(s, 49_999_999)

    def test_smaller_card_stake_is_kept(self):
        s, rec = c0.c0_stake({"stake_sol": 0.01, "size": 0.25})
        self.assertEqual(s, 10_000_000)

    def test_cap_argument_only_tightens(self):
        self.assertEqual(c0.c0_stake({"stake_sol": 0.5}, 20_000_000)[0], 20_000_000)
        self.assertEqual(c0.c0_stake({"stake_sol": 0.5}, 90_000_000)[0], 50_000_000)

    def test_invalid_rows_refused(self):
        self.assertEqual(c0.c0_stake({"stake_sol": 0.05, "size": 0.3})[1]["refused"], "size_invalid")
        self.assertEqual(c0.c0_stake({"size": True, "stake_sol": 0.05})[1]["refused"], "size_invalid")
        for bad in (None, 0, -0.05, float("nan"), "0.05", True):
            s, rec = c0.c0_stake({"stake_sol": bad})
            self.assertIsNone(s, bad)
            self.assertEqual(rec["refused"], "card_stake_invalid")

    def test_size_only_row_uses_size_as_card(self):
        self.assertEqual(c0.c0_stake({"size": 0.5})[0], 50_000_000)

    def test_total_stop_committed(self):
        R = alb.RENT_RESERVE_LAMPORTS
        w = lambda st: c0.position_worst_case_lamports(st, R)
        self.assertEqual(w(50_000_000), 50_000_000 + c0.TRIP_FEE_RESERVE_LAMPORTS + R + c0.SELL_FEE_RESERVE_LAMPORTS)
        tot = c0.C0_LIMITS["total_loss_lamports"]
        # realized term: exactly 0.20 passes, one lamport over refuses
        self.assertIsNone(c0.total_stop_problem(tot - w(1), [], 1, R))
        self.assertEqual(c0.total_stop_problem(tot - w(1) + 1, [], 1, R), "total_loss_committed")
        # open-position term (stake + buy fee + rent + sell fee each), then the new buy's own worst case
        stake_ok = tot - w(50_000_000) - w(0)  # new stake such that committed + new worst case == total
        self.assertIsNone(c0.total_stop_problem(0, [50_000_000], stake_ok, R))
        self.assertEqual(c0.total_stop_problem(0, [50_000_000], stake_ok + 1, R), "total_loss_committed")
        # each position adds exactly its own worst case: moving 1 lamport of stake from open to open changes nothing
        self.assertEqual(c0.committed_loss_lamports(0, [10, 20], R), w(10) + w(20))
        # new-buy term: fee + rent + sell reserve are counted for the new buy too
        self.assertIsNone(c0.total_stop_problem(0, [], tot - w(0), R))
        self.assertEqual(c0.total_stop_problem(0, [], tot - w(0) + 1, R), "total_loss_committed")
        # the old stake-only arithmetic would have admitted this
        self.assertEqual(c0.total_stop_problem(100_000_000, [50_000_000], 50_000_000, R), "total_loss_committed")
        self.assertEqual(c0.committed_loss_lamports(-5, [], R), 0)  # a realized gain does not offset exposure

    def base(self, **over):
        return {"bot_id": "hv4-CON-F", "frozen_sha": c0.FROZEN_SHA, "root": "/r", "state_dir": "/s", "end_ms": 1, "max_hold_ms": 1, **raw(**over)}

    def test_defaults_are_c0(self):
        cfg = alb.Config.from_dict(self.base())
        self.assertEqual((cfg.stake_lamports, cfg.max_open, cfg.max_trades_per_day, cfg.daily_loss_lamports, cfg.total_loss_lamports),
                         (50_000_000, 2, 30, 100_000_000, 200_000_000))
        self.assertEqual(cfg.wallet_floor_lamports, FUNDED - 250_000_000)

    def test_config_only_tightens(self):
        cfg = alb.Config.from_dict(self.base(stake_lamports=90_000_000, max_open=5, max_trades_per_day=31, daily_loss_lamports=10**9, total_loss_lamports=10**9))
        self.assertEqual((cfg.stake_lamports, cfg.max_open, cfg.max_trades_per_day, cfg.daily_loss_lamports, cfg.total_loss_lamports),
                         (50_000_000, 2, 30, 100_000_000, 200_000_000))
        cfg = alb.Config.from_dict(self.base(stake_lamports=10_000_000, max_open=1))
        self.assertEqual((cfg.stake_lamports, cfg.max_open), (10_000_000, 1))

    def test_refusals_raise(self):
        for over, why in (({"bot_id": "hv4-CON-L"}, "arm_not_canary_candidate"), ({"bot_id": "hv4-BIG-P"}, "arm_not_canary_candidate"),
                          ({"bot_id": "hv4-BIG-R"}, "arm_not_canary_candidate"), ({"bot_id": "bot-x"}, "arm_not_hv4"),
                          ({"priority_lamports": 500_000}, "priority_lamports_not_pin"),
                          ({"priority_pin": {"profile": "p155", "buy": 155_000, "sell": 155_000, "escalated": 155_000}}, "priority_pin_mismatch")):
            with self.assertRaises(ValueError) as cm:
                alb.Config.from_dict(self.base(**over))
            self.assertIn(why, str(cm.exception))
        with self.assertRaises(ValueError):
            alb.Config.from_dict(self.base(mode="live", funded_lamports=349_999_999))
        d = self.base(mode="live")
        del d["funded_lamports"]
        with self.assertRaises(ValueError):
            alb.Config.from_dict(d)

    def test_plain_config_rejects_c0_keys(self):
        d = {k: v for k, v in self.base().items() if k != "profile"}
        with self.assertRaises(ValueError):
            alb.Config.from_dict(d)
        with self.assertRaises(ValueError):
            alb.Config.from_dict({**self.base(), "profile": "other"})


class BridgeC0Tests(TmpCase):
    def test_entry_sized_to_s_and_mapping_logged(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1", stake_sol=0.25, size=0.25)
        b = e.bridge()
        b.tick()
        self.assertEqual([c[3] for c in e.port.calls if c[0] == "buy"], [50_000_000])
        sent = e.log_rows("buy_sent")[0]
        self.assertEqual(sent["stake_lamports"], 50_000_000)
        o = next(iter(b.state.orders.values()))
        self.assertEqual(o["size_map"]["twin_stake_lamports"], o["stake_lamports"])
        self.assertTrue(o["size_map"]["clamped"])

    def test_invalid_size_refused(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1", stake_sol=0.05, size=0.3)
        e.bridge().tick()
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "size_invalid")

    def test_max_open_and_daily_edges(self):
        e = Hv4Env(self.tmp)
        e.start()
        for i in range(3):
            e.entry_sz(f"o{i}", pool=f"P{i}")
        b = e.bridge()
        b.tick()
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 2)  # exactly 2 open, the third refused
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "max_open")

    def test_total_stop_counts_open_exposure(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1", pool="P1")
        e.entry_sz("o2", pool="P2")
        b = e.bridge()
        w = c0.position_worst_case_lamports(50_000_000, alb.RENT_RESERVE_LAMPORTS)
        # first buy: realized + w == 0.20 exactly is allowed; the second then adds another w and is refused (open term, an order still buy_sent/open)
        b.state.realized_lamports = -(c0.C0_LIMITS["total_loss_lamports"] - w)
        b.tick()
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "total_loss_committed")

    def test_total_stop_one_lamport_over_refused_and_pending_counts(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1", pool="P1")
        b = e.bridge()
        w = c0.position_worst_case_lamports(50_000_000, alb.RENT_RESERVE_LAMPORTS)
        b.state.realized_lamports = -(c0.C0_LIMITS["total_loss_lamports"] - w + 1)
        b.tick()
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "total_loss_committed")
        # a pending (buy_sending, in-flight) order is inside committed loss
        (Path(self.tmp) / "p").mkdir()
        e2 = Hv4Env(str(Path(self.tmp) / "p"))
        e2.start()
        e2.entry_sz("o1", pool="P1")
        b2 = e2.bridge()
        b2.state.orders["x"] = {"order_id": "x", "pool": "PX", "mint": "MX", "status": "buy_sending", "stake_lamports": 50_000_000, "live": False}
        b2.state.realized_lamports = -(c0.C0_LIMITS["total_loss_lamports"] - 2 * w + 1)
        b2.tick()
        self.assertEqual([c for c in e2.port.calls if c[0] == "buy"], [])
        self.assertEqual(e2.log_rows("entry_refused")[-1]["why"], "total_loss_committed")

    def _main(self, e, live=False, root=None, marker=False):
        import contextlib, io, json
        full = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
        if root:
            full["root"] = root
        elif marker:
            Path(full["root"]).mkdir(parents=True, exist_ok=True)
            (Path(full["root"]) / c0.SYNTHETIC_MARKER).write_text("")
        paths, st = live_main_env(self.tmp, full["bot_id"])
        if live:
            full.update(paths)
        cfgp = Path(self.tmp) / "cfg_main.json"
        cfgp.write_text(json.dumps({**full, "mode": "live" if live else "dry"}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), st:
            rc = alb.main(["--config", str(cfgp), "--once"] + (["--live"] if live else []))
        return rc, buf.getvalue()

    @mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", True)  # the default state, pinned: flipping the constant to False must not change what this test asserts
    def test_dry_start_without_marker_refused(self):
        rc, out = self._main(Hv4Env(self.tmp))
        self.assertEqual(rc, 2)
        self.assertIn("hv4_c0_incomplete", out)

    def test_dry_start_on_marked_synthetic_root_allowed_but_live_never(self):
        rc, out = self._main(Hv4Env(self.tmp), marker=True)
        self.assertEqual(rc, 0, out)
        rc, out = self._main(Hv4Env(self.tmp), live=True, marker=True)
        self.assertEqual(rc, 2)

    @mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", True)  # the default state, pinned: flipping the constant to False must not change what this test asserts
    def test_arena_root_always_refused_without_touching_it(self):
        for root in ("/data/mal/arena", "/data/mal/arena/haiku-v4", "/data/mal/arena/../arena/x", "/data/mal/arena/nonexistent-synth"):
            for live in (False, True):
                rc, out = self._main(Hv4Env(self.tmp), live=live, root=root)
                self.assertEqual(rc, 2, (root, live))
                self.assertIn("hv4_c0_incomplete", out)
        self.assertEqual(c0.start_refusal("/data/mal/arena/x", False), "hv4_c0_incomplete")

    def test_hv4_bot_needs_profile_every_arm_every_mode(self):
        for name in hv4_arms.ARMS:
            for mode in ("dry", "live"):
                base = {"bot_id": "hv4-" + name, "frozen_sha": "a" * 64, "root": "/nonexistent-synth", "state_dir": "/nonexistent-synth/s", "end_ms": 1, "max_hold_ms": 1, "mode": mode}
                with self.assertRaises(ValueError, msg=(name, mode)):
                    alb.Config.from_dict(base)
                with self.assertRaises(ValueError):
                    alb.Config.from_dict({**base, "profile": ""})

    @mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", True)  # the default state, pinned: flipping the constant to False must not change what this test asserts
    def test_live_start_refused_hv4_c0_incomplete(self):
        import contextlib, io, json
        cfgp = Path(self.tmp) / "cfg.json"
        e = Hv4Env(self.tmp)
        full = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
        paths, st = live_main_env(self.tmp, full["bot_id"])
        cfgp.write_text(json.dumps({**full, **paths, "mode": "live"}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), st:
            rc = alb.main(["--config", str(cfgp), "--live"])
        self.assertEqual(rc, 2)
        self.assertIn("hv4_c0_incomplete", buf.getvalue())

    def test_floor_uses_pin_reserves(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        fl = b.cfg.wallet_floor_lamports  # 0.35 SOL
        need = fl + 50_000_000 + c0.TRIP_FEE_RESERVE_LAMPORTS + alb.RENT_RESERVE_LAMPORTS + alb.BUFFER_LAMPORTS
        self.assertIsNone(b.floor_problem(50_000_000, need))
        self.assertEqual(b.floor_problem(50_000_000, need - 1), "wallet_floor")

    def test_tier_t1_and_live_ok_naming_bot(self):
        e = Hv4Env(self.tmp, live=True)
        b = e.bridge()
        self.assertEqual(b.tier_limits()[2], "tier_file_missing")
        e.tier.write_text("T0\n")
        self.assertEqual(b.tier_limits()[2], "tier_file_invalid")
        e.tier.write_text("T1\n")
        tier, lim, prob = b.tier_limits()
        self.assertEqual((tier, prob, lim["stake_lamports"]), ("T1", None, 50_000_000))
        e.live_ok.write_text("hv4-other-X\n")
        self.assertEqual(b.effective_live(), (False, "live_ok_other_book"))
        e.live_ok.write_text(ARM + "\n")
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_dec_missing"))  # no owner file, no live send
        b.owner_dec_path = owner_fixture(self.tmp, ARM)
        self.assertEqual(b.effective_live(), (True, None))

    def test_live_entry_refused_without_t1(self):
        e = Hv4Env(self.tmp, live=True)
        e.live_ok.write_text(ARM + "\n")
        e.start()
        e.entry_sz("o1")
        e.bridge().tick()
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "tier_file_missing")


class H5PathUnchangedTests(TmpCase):
    def test_default_config_has_no_c0_fields(self):
        e = Env(self.tmp)
        cfg = e.bridge().cfg
        self.assertEqual((cfg.profile, cfg.funded_lamports), ("", None))
        self.assertEqual((cfg.stake_lamports, cfg.total_loss_lamports, cfg.wallet_floor_lamports), (20_000_000, 150_000_000, alb.DEFAULT_WALLET_FLOOR))
        self.assertEqual(alb.TIERS["T0"]["stake_lamports"], 20_000_000)
        self.assertEqual((alb.FEE_RESERVE_LAMPORTS, alb.SELL_FEE_RESERVE_LAMPORTS), (1_010_000, 505_000))

    def test_t0_tier_unchanged_and_no_size_map(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual(b.tier_limits()[0:3:2], ("T0", "tier_file_missing"))
        o = next(iter(b.state.orders.values()))
        self.assertNotIn("size_map", o)
        self.assertEqual(o["stake_lamports"], 20_000_000)
        e.tier.write_text("T1\n")
        self.assertEqual(b.tier_limits()[0:3:2], ("T0", "tier_file_invalid"))  # T1 still not a T0-bridge tier

    def test_plain_live_ok_content_not_inspected(self):
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("anything\n")
        self.assertEqual(e.bridge().effective_live(), (True, None))

    def test_live_priority_still_500k_for_plain(self):
        src = Path(alb.__file__).read_text()
        self.assertIn('else 500_000, "max_open"', src)


if __name__ == "__main__":
    unittest.main()
