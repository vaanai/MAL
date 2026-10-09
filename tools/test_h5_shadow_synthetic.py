"""Synthetic-migration gate of the H5 shadow (EXP-024 Amendment 4 / DEC-024 Amendment 2): a pool whose CompleteEvent tx carries a PostCompleteBuyEvent,
or whose class is unknown at trigger time, gets no trigger record and no outcome record, ever.

Fixture tools/fixtures/h5_shadow/completing_txs_20261009.json: structure-only copies of real post-redeploy curve-completing txs from public RPC
(Program data log lines and emit_cpi inner-instruction data; no amounts are read)."""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from tools import h5_shadow as h5
from tools import pump_structure_monitor as M
from tools.test_h5_shadow import MINT, POOL, SOL, SPS, Tape, announce, boost_buys, drain, fixture_notice, make_engine, run, types

FX = json.loads((Path(__file__).parent / "fixtures" / "h5_shadow" / "completing_txs_20261009.json").read_text())["txs"]
SYN = [t for t in FX if t["synthetic"]]
PLAIN = [t for t in FX if not t["synthetic"]]
MX, MY = SYN[0]["mint"], PLAIN[0]["mint"]  # valid base58 mints for the paths that derive the curve PDA
EXCLUDED_KEYS = {"type", "reason", "pool", "mint", "s0", "slot", "v", "schema", "t_ms"}


def ws_note(tx, *, failed=False, truncated=False, extra_logs=()):
    logs = [f"Program {M.PUMP_PROGRAM} invoke [1]", "Program log: Instruction: BuyV3", *tx["program_data_lines"], *extra_logs]
    if truncated:
        logs.insert(3, "Log truncated")
    return NS(slot=tx["slot"], signature=tx["signature"], failed=failed, logs=tuple(logs), t_recv_ms=1, commitment="confirmed", feed="x")


def rpc_tx(tx, *, with_logs=True, with_cpi=True):
    inner = [{"index": 0, "instructions": [{"programIdIndex": 0, "data": c["data"]} for c in tx["emit_cpi_inner"]]}] if with_cpi else []
    return {"slot": tx["slot"], "transaction": {"message": {"accountKeys": [M.PUMP_PROGRAM]}},
            "meta": {"logMessages": list(tx["program_data_lines"]) if with_logs else [], "innerInstructions": inner}}


def _pcb_line():
    """A real PostCompleteBuyEvent 'Program data:' line from a recorded synthetic completing tx."""
    for ln in SYN[0]["program_data_lines"]:
        raw = h5._program_data_bytes(ln)
        if raw and raw[:8] == M.DISC_POST_COMPLETE_BUY:
            return ln
    raise AssertionError("no PostCompleteBuyEvent line in the synthetic fixture")


def mig_raw(mint):
    """A CompletePumpAmmMigrationEvent blob for `mint` (layout: monitor.decode_migration_event)."""
    raw = M.DISC_MIGRATE + b"\0" * 32 + M.b58decode(mint) + b"\0" * 24 + b"\1" * 32 + (1_800_000_000).to_bytes(8, "little") + b"\2" * 32 + b"\0" * 32
    assert M.decode_migration_event(raw)["mint"] == mint
    return raw


def mig_line(mint):
    return "Program data: " + __import__("base64").b64encode(mig_raw(mint)).decode()


def migrate_cut_tx(mint, *, pcb=False):
    """A migrate tx as the chain returns it in practice: the logs are cut after the CreatePool line, the pump events sit in emit_cpi inner instructions only."""
    blobs = [mig_raw(mint)]
    if pcb:
        blobs.append(h5._program_data_bytes(_pcb_line()))
    inner = [{"programIdIndex": 0, "data": M.b58encode(M.EVENT_CPI_TAG + b)} for b in blobs]
    return {"slot": 2, "transaction": {"message": {"accountKeys": [M.PUMP_PROGRAM]}},
            "meta": {"logMessages": ["Program log: Instruction: Migrate", "Log truncated"], "innerInstructions": [{"index": 0, "instructions": inner}]}}


def migrate_note(mint, *, pcb=False, failed=False, truncated=False, with_complete=False):
    """The PumpSwap CreatePool notice = the pool's migrate tx."""
    logs = [f"Program {M.PUMP_PROGRAM} invoke [1]", "Program log: Instruction: Migrate", mig_line(mint)]
    if pcb:
        logs.append(_pcb_line())
    if with_complete:
        logs += [ln for ln in next(t for t in FX if t["mint"] == mint)["program_data_lines"] if "Program data" in ln]
    if truncated:
        logs.insert(2, "Log truncated")
    return NS(slot=2, signature="migrate", failed=failed, logs=tuple(logs), t_recv_ms=1, commitment="confirmed", feed="x")


def migrate_rpc_tx(mint, *, pcb=False):
    lines = [mig_line(mint)] + ([_pcb_line()] if pcb else [])
    return {"slot": 2, "transaction": {"message": {"accountKeys": [M.PUMP_PROGRAM]}}, "meta": {"logMessages": lines, "innerInstructions": []}}


def other_tx(slot=1):
    return {"slot": slot, "transaction": {"message": {"accountKeys": [M.PUMP_PROGRAM]}}, "meta": {"logMessages": ["Program log: Instruction: Migrate"], "innerInstructions": []}}


class FakeRpc:
    """Stands in for monitor.RpcClient: .call(method, params) and .total_calls."""

    def __init__(self, sigs, txs, delay=0.0):
        self.sigs, self.txs, self.delay, self.log = sigs, txs, delay, []

    @property
    def total_calls(self):
        return len(self.log)

    def call(self, method, params):
        self.log.append((method, params))
        if self.delay:
            time.sleep(self.delay)
        if method == "getSignaturesForAddress":
            return self.sigs
        if method == "getTransaction":
            return self.txs.get(params[0])
        raise AssertionError(method)


def scenario(eng, *, tail=True):
    """A pool that triggers: BOOST buys, then a drain sell that leaves Q <= 40 SOL (the standard fixture of test_h5_shadow)."""
    announce(eng)
    t = Tape()
    t.row(1000, "buy", "A", SOL // 10)
    boost_buys(t, 1000)
    drain(t, 1110, 35.0)
    if tail:
        t.row(1200, "buy", "Z", SOL // 20)
    return t


class FixtureTests(unittest.TestCase):
    def test_fixture_has_both_classes(self):
        self.assertGreaterEqual(len(SYN), 3)
        self.assertGreaterEqual(len(PLAIN), 3)
        for tx in FX:  # structure only: no field beyond what the classifier needs
            self.assertEqual(set(tx), {"signature", "slot", "mint", "synthetic", "program_data_lines", "emit_cpi_inner", "logs_truncated", "n_logs"})
            self.assertFalse(tx["logs_truncated"])
            self.assertGreaterEqual(tx["slot"], 454_596_459)  # post-redeploy


class WsClassificationTests(unittest.TestCase):
    def test_every_fixture_completing_tx_gives_the_comp_part_like_the_monitor(self):
        clf = h5.SynClassifier()
        for tx in FX:
            self.assertEqual(clf.observe_notice(ws_note(tx)), 1)
            blobs = [b for b in (h5._program_data_bytes(ln) for ln in tx["program_data_lines"]) if b]
            seen = M.post_complete_buy_seen(blobs, tx["mint"])[0]  # the monitor's function, not a copy
            self.assertEqual(seen, tx["synthetic"])  # and it is the recorded truth
            self.assertEqual(clf._m[tx["mint"]]["comp"], (seen, "ws"))
            # synthetic is decided by the completing tx alone; plain also needs the migrate tx
            self.assertEqual(clf.lookup(tx["mint"]), (True, "ws") if seen else (None, None))
        self.assertEqual(clf.lookup("nope"), (None, None))

    def test_plain_needs_both_txs_and_synthetic_needs_either(self):
        plain, syn = PLAIN[0], SYN[0]
        clf = h5.SynClassifier()
        clf.observe_notice(ws_note(plain))
        self.assertEqual(clf.lookup(plain["mint"]), (None, None))  # the migrate tx has not been read: unclassified
        self.assertEqual(clf.observe_migrate_notice(plain["mint"], migrate_note(plain["mint"])), 1)
        self.assertEqual(clf.lookup(plain["mint"]), (False, "ws"))
        # the migrate tx alone, no completing tx read
        clf2 = h5.SynClassifier()
        clf2.observe_migrate_notice(syn["mint"], migrate_note(syn["mint"]))
        self.assertEqual(clf2.lookup(syn["mint"]), (None, None))
        # ... but a PostCompleteBuyEvent in it is conclusive without the completing tx
        clf2.observe_migrate_notice(PLAIN[1]["mint"], migrate_note(PLAIN[1]["mint"], pcb=True))
        self.assertEqual(clf2.lookup(PLAIN[1]["mint"]), (True, "ws"))

    def test_event_only_in_the_migrate_tx_gives_synthetic(self):
        plain = PLAIN[0]
        clf = h5.SynClassifier()
        clf.observe_notice(ws_note(plain))  # the CompleteEvent tx is clean
        clf.observe_migrate_notice(plain["mint"], migrate_note(plain["mint"], pcb=True))  # the PostCompleteBuyEvent rides in the migrate tx only
        self.assertEqual(clf._m[plain["mint"]]["comp"], (False, "ws"))
        self.assertEqual(clf._m[plain["mint"]]["mig"], (True, "ws"))
        self.assertEqual(clf.lookup(plain["mint"]), (True, "ws"))
        # the other order gives the same answer
        clf = h5.SynClassifier()
        clf.observe_migrate_notice(plain["mint"], migrate_note(plain["mint"], pcb=True))
        clf.observe_notice(ws_note(plain))
        self.assertEqual(clf.lookup(plain["mint"]), (True, "ws"))

    def test_a_migrate_tx_that_carries_the_complete_event_is_both_parts(self):
        plain = PLAIN[0]
        clf = h5.SynClassifier()
        self.assertEqual(clf.observe_migrate_notice(plain["mint"], migrate_note(plain["mint"], with_complete=True)), 2)
        self.assertEqual(clf.lookup(plain["mint"]), (False, "ws"))
        clf2 = h5.SynClassifier()
        clf2.observe_migrate_notice(SYN[0]["mint"], migrate_note(SYN[0]["mint"], with_complete=True))
        self.assertEqual(clf2.lookup(SYN[0]["mint"]), (True, "ws"))

    def test_the_pump_feed_also_reads_a_migrate_notice_it_sees(self):
        plain = PLAIN[0]
        clf = h5.SynClassifier()
        clf.observe_notice(ws_note(plain))
        self.assertEqual(clf.observe_notice(migrate_note(plain["mint"], pcb=True)), 1)
        self.assertEqual(clf.lookup(plain["mint"]), (True, "ws"))

    def test_failed_truncated_and_unrelated_notices_classify_nothing(self):
        clf = h5.SynClassifier()
        self.assertEqual(clf.observe_notice(ws_note(SYN[0], failed=True)), 0)
        self.assertEqual(clf.observe_migrate_notice(SYN[0]["mint"], migrate_note(SYN[0]["mint"], failed=True)), 0)
        # truncated logs and no PostCompleteBuyEvent visible: the cut part may hold it, so nothing is recorded (the RPC path reads the inner instructions)
        self.assertEqual(clf.observe_notice(ws_note(PLAIN[0], truncated=True)), 0)
        self.assertEqual(clf.observe_migrate_notice(PLAIN[0]["mint"], migrate_note(PLAIN[0]["mint"], truncated=True)), 0)
        self.assertEqual(clf.lookup(PLAIN[0]["mint"]), (None, None))
        self.assertEqual(clf.stats["ws_truncated"], 2)
        # a PostCompleteBuyEvent that IS visible is conclusive even in a truncated log
        self.assertEqual(clf.observe_notice(ws_note(SYN[0], truncated=True)), 1)
        self.assertEqual(clf.lookup(SYN[0]["mint"]), (True, "ws"))
        self.assertEqual(clf.observe_notice(NS(failed=False, logs=("Program log: hi",), slot=1, signature="s")), 0)
        trade_only = NS(failed=False, logs=("Program data: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",), slot=1, signature="s")
        self.assertEqual(clf.observe_notice(trade_only), 0)

    def test_true_is_sticky_on_conflict(self):
        clf = h5.SynClassifier()
        clf.record("m", False, "ws")
        clf.record("m", True, "rpc")
        self.assertEqual(clf.lookup("m"), (True, "rpc"))
        clf.record("m", False, "ws")
        self.assertEqual(clf.lookup("m"), (True, "rpc"))
        self.assertGreaterEqual(clf.stats["conflicts"], 2)

    def test_memory_is_bounded(self):
        clf = h5.SynClassifier(max_n=3)
        for i in range(10):
            clf.record(f"m{i}", False, "ws")
        self.assertEqual(clf.lookup("m0"), (None, None))
        self.assertEqual(clf.lookup("m9"), (False, "ws"))


class PumpSideFeedTests(unittest.TestCase):
    def test_side_feed_classifies_survives_a_bad_notice_and_a_source_restart(self):
        clf = h5.SynClassifier()
        calls = []

        class Src:
            def __init__(self, n):
                self.n = n

            async def notices(self, stop):
                if self.n == 0:
                    yield ws_note(SYN[0])
                    yield NS(failed=False, logs=None, slot=1, signature="bad")  # observe_notice raises on this
                    raise RuntimeError("socket died")
                yield ws_note(PLAIN[0])
                stop.set()

        def factory():
            calls.append(1)
            return Src(len(calls) - 1)

        async def go():
            stop = asyncio.Event()
            errs = []
            await h5.run_pump_feed(factory, clf, stop, sleep=lambda s: asyncio.sleep(0), on_error=lambda e, c: errs.append(c["where"]))
            return errs

        errs = asyncio.run(go())
        self.assertEqual(errs, ["syn_ws"])
        self.assertEqual(clf.lookup(SYN[0]["mint"]), (True, "ws"))
        self.assertEqual(clf._m[PLAIN[0]["mint"]]["comp"], (False, "ws"))  # the completing tx is clean; plain still waits for the migrate tx
        self.assertEqual((clf.stats["ws_decode_errors"], clf.stats["ws_feed_errors"], len(calls)), (1, 1, 2))

    def test_side_feed_is_its_own_source_object_and_subscribes_the_pump_program(self):
        try:
            import websockets  # noqa: F401
            import certifi  # noqa: F401
        except ImportError:
            self.skipTest("websockets/certifi not in this venv (the listener venv has them)")
        src = h5.build_pump_source([], 1, "confirmed")
        self.assertEqual(src.programs, ("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",))
        self.assertEqual(h5.build_pump_source([], 2, "confirmed").programs, src.programs)


class RpcFallbackTests(unittest.TestCase):
    def curve(self, mint):
        return M.find_program_address([b"bonding-curve", M.b58decode(mint)], M.PUMP_PROGRAM)[0]

    def test_classify_via_rpc_finds_both_txs_behind_other_txs_and_skips_failed_ones(self):
        for tx in PLAIN:
            sigs = [{"signature": "failed1", "err": {"x": 1}}, {"signature": "migrate"}, {"signature": "noise"}, {"signature": tx["signature"]}]
            c = FakeRpc(sigs, {"migrate": migrate_rpc_tx(tx["mint"]), "noise": other_tx(), tx["signature"]: rpc_tx(tx)})
            self.assertEqual(h5.classify_via_rpc(c, tx["mint"]), {"mig": False, "comp": False})
            self.assertEqual(c.log[0][0], "getSignaturesForAddress")
            self.assertEqual(c.log[0][1][0], self.curve(tx["mint"]))  # the bonding-curve PDA of the mint
            self.assertEqual([m for m, _ in c.log].count("getTransaction"), 3)  # the failed one was never fetched
            self.assertTrue(all(p[1]["commitment"] == "confirmed" for m, p in c.log if m == "getTransaction"))

    def test_classify_via_rpc_stops_at_the_first_true(self):
        tx = SYN[0]
        sigs = [{"signature": "migrate"}, {"signature": tx["signature"]}, {"signature": "never"}]
        c = FakeRpc(sigs, {"migrate": migrate_rpc_tx(tx["mint"]), tx["signature"]: rpc_tx(tx)})
        self.assertEqual(h5.classify_via_rpc(c, tx["mint"]), {"mig": False, "comp": True})
        self.assertEqual([m for m, _ in c.log].count("getTransaction"), 2)

    def test_event_only_in_the_migrate_tx_gives_synthetic_through_the_rpc_path(self):
        tx = PLAIN[0]
        sigs = [{"signature": "migrate"}, {"signature": tx["signature"]}]
        c = FakeRpc(sigs, {"migrate": migrate_rpc_tx(tx["mint"], pcb=True), tx["signature"]: rpc_tx(tx)})
        self.assertEqual(h5.classify_via_rpc(c, tx["mint"]), {"mig": True})  # conclusive at once: the completing tx need not be read
        clf = h5.SynClassifier()

        async def go():
            fb = h5.RpcFallback(clf, client_factory=lambda: c, delays=(0.0,), sleep=lambda s: asyncio.sleep(0))
            fb.request(tx["mint"])
            await asyncio.gather(*list(fb._inflight.values()))

        asyncio.run(go())
        self.assertEqual(clf.lookup(tx["mint"]), (True, "rpc"))

    def test_the_migrate_tx_is_read_by_its_signature_in_one_call_even_with_cut_logs(self):
        tx = PLAIN[0]
        c = FakeRpc([], {"migsig": migrate_cut_tx(tx["mint"])})
        self.assertEqual(h5.classify_via_rpc(c, tx["mint"], "migsig", {"mig"}), {"mig": False})
        self.assertEqual([m for m, _ in c.log], ["getTransaction"])  # no getSignaturesForAddress when only the migrate tx is needed
        c = FakeRpc([], {"migsig": migrate_cut_tx(tx["mint"], pcb=True)})
        self.assertEqual(h5.classify_via_rpc(c, tx["mint"], "migsig", {"mig"}), {"mig": True})  # event only in the (cut-log) migrate tx: synthetic

    def test_with_both_parts_needed_the_migrate_tx_is_not_fetched_twice(self):
        tx = PLAIN[0]
        c = FakeRpc([{"signature": "migsig"}, {"signature": tx["signature"]}], {"migsig": migrate_cut_tx(tx["mint"]), tx["signature"]: rpc_tx(tx)})
        self.assertEqual(h5.classify_via_rpc(c, tx["mint"], "migsig", {"comp", "mig"}), {"mig": False, "comp": False})
        self.assertEqual([p[0] for m, p in c.log if m == "getTransaction"], ["migsig", tx["signature"]])

    def test_a_signature_that_is_not_this_mints_migrate_tx_gives_nothing(self):
        tx = PLAIN[0]
        c = FakeRpc([], {"migsig": migrate_cut_tx(PLAIN[1]["mint"])})  # another mint's migrate tx
        self.assertEqual(h5.classify_via_rpc(c, tx["mint"], "migsig", {"mig"}), {})

    def test_request_with_the_migrate_signature_costs_one_call_when_the_completing_tx_is_known(self):
        tx = PLAIN[0]
        clf = h5.SynClassifier()
        clf.record_part(tx["mint"], "comp", False, "ws")
        fake = FakeRpc([], {"migsig": migrate_cut_tx(tx["mint"])})

        async def go():
            fb = h5.RpcFallback(clf, client_factory=lambda: fake, delays=(0.0,), sleep=lambda s: asyncio.sleep(0))
            self.assertTrue(fb.request(tx["mint"], "migsig"))
            await asyncio.gather(*list(fb._inflight.values()))
            self.assertFalse(fb.request(tx["mint"], "migsig"))  # settled, and never asked twice
            clf.record_part("MintNew", "comp", True, "ws")
            self.assertFalse(fb.request("MintNew"))  # already known synthetic: nothing to read

        asyncio.run(go())
        self.assertEqual(clf.lookup(tx["mint"]), (False, "rpc"))
        self.assertEqual(clf.stats["rpc_calls"], 1)

    def test_a_given_up_mint_is_not_asked_again_at_s0(self):
        clf = h5.SynClassifier()

        async def go():
            fb = h5.RpcFallback(clf, client_factory=lambda: FakeRpc([], {}), delays=(0.0,), sleep=lambda s: asyncio.sleep(0))
            self.assertTrue(fb.request(MX, "migsig"))
            await asyncio.gather(*list(fb._inflight.values()))
            self.assertFalse(fb.request(MX))

        asyncio.run(go())
        self.assertEqual((clf.stats["rpc_requests"], clf.stats["rpc_gave_up"]), (1, 1))

    def test_only_one_of_the_two_txs_found_leaves_a_plain_looking_pool_unclassified(self):
        tx = PLAIN[0]
        for sigs, txs in (([{"signature": tx["signature"]}], {tx["signature"]: rpc_tx(tx)}),  # the migrate tx is missing
                          ([{"signature": "migrate"}], {"migrate": migrate_rpc_tx(tx["mint"])})):  # the completing tx is missing
            clf = h5.SynClassifier()

            async def go():
                fb = h5.RpcFallback(clf, client_factory=lambda: FakeRpc(sigs, txs), delays=(0.0, 0.0), sleep=lambda s: asyncio.sleep(0))
                fb.request(tx["mint"])
                await asyncio.gather(*list(fb._inflight.values()))

            asyncio.run(go())
            self.assertEqual(clf.lookup(tx["mint"]), (None, None))
            self.assertEqual(clf.stats["rpc_gave_up"], 1)

    def test_the_event_is_read_from_logs_alone_or_from_the_emit_cpi_inner_instruction_alone(self):
        for tx in FX:
            for kw in ({"with_cpi": False}, {"with_logs": False}):
                c = FakeRpc([{"signature": tx["signature"]}], {tx["signature"]: rpc_tx(tx, **kw)})
                self.assertEqual(h5.classify_via_rpc(c, tx["mint"]), {"comp": tx["synthetic"]}, kw)

    def test_not_found_is_none_and_is_bounded(self):
        sigs = [{"signature": f"s{i}"} for i in range(20)]
        c = FakeRpc(sigs, {s["signature"]: other_tx() for s in sigs})
        self.assertEqual(h5.classify_via_rpc(c, SYN[0]["mint"]), {})
        self.assertEqual(c.total_calls, 1 + h5.RPC_TX_PER_ATTEMPT)
        self.assertEqual(h5.classify_via_rpc(FakeRpc([], {}), SYN[0]["mint"]), {})
        self.assertEqual(h5.classify_via_rpc(FakeRpc(None, {}), SYN[0]["mint"]), {})

    def test_a_completion_event_for_another_mint_is_not_this_mints_class(self):
        a, b = SYN[0], PLAIN[0]
        c = FakeRpc([{"signature": a["signature"]}], {a["signature"]: rpc_tx(a)})
        self.assertEqual(h5.classify_via_rpc(c, b["mint"]), {})

    def test_request_returns_at_once_and_the_lookup_lands_in_the_classifier(self):
        tx = PLAIN[0]
        clf = h5.SynClassifier()
        fake = FakeRpc([{"signature": "migrate"}, {"signature": tx["signature"]}], {"migrate": migrate_rpc_tx(tx["mint"]), tx["signature"]: rpc_tx(tx)}, delay=0.2)

        async def go():
            fb = h5.RpcFallback(clf, client_factory=lambda: fake, delays=(0.0,), sleep=lambda s: asyncio.sleep(0))
            t0 = time.monotonic()
            self.assertTrue(fb.request(tx["mint"]))
            self.assertLess(time.monotonic() - t0, 0.05)  # request() never waits on the RPC
            self.assertFalse(fb.request(tx["mint"]))  # already in flight
            self.assertEqual(clf.lookup(tx["mint"]), (None, None))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if clf.lookup(tx["mint"])[0] is not None:
                    break
            await fb.close()

        asyncio.run(go())
        self.assertEqual(clf.lookup(tx["mint"]), (False, "rpc"))
        self.assertEqual((clf.stats["rpc_requests"], clf.stats["rpc_attempts"], clf.stats["rpc_comp_plain"], clf.stats["rpc_mig_plain"]), (1, 1, 1, 1))
        self.assertEqual(clf.stats["rpc_calls"], 3)  # one getSignaturesForAddress + two getTransaction

    def test_retries_then_gives_up_leaving_the_mint_unclassified(self):
        clf = h5.SynClassifier()
        fake = FakeRpc([], {})
        made = []

        async def go():
            fb = h5.RpcFallback(clf, client_factory=lambda: made.append(1) or fake, delays=(0.0, 0.0, 0.0), sleep=lambda s: asyncio.sleep(0))
            fb.request(MX)
            await asyncio.gather(*list(fb._inflight.values()))

        asyncio.run(go())
        self.assertEqual((len(made), clf.stats["rpc_gave_up"]), (3, 1))
        self.assertEqual(clf.lookup(MX), (None, None))

    def test_a_slow_rpc_times_out_and_an_erroring_one_is_counted(self):
        clf = h5.SynClassifier()

        class Boom:
            total_calls = 0

            def call(self, method, params):
                raise M.RpcError("http 400")

        async def go():
            slow = h5.RpcFallback(clf, client_factory=lambda: FakeRpc([], {}, delay=0.4), delays=(0.0,), attempt_timeout_s=0.05, sleep=lambda s: asyncio.sleep(0))
            slow.request(MX)
            await asyncio.gather(*list(slow._inflight.values()))
            bad = h5.RpcFallback(clf, client_factory=Boom, delays=(0.0, 0.0), sleep=lambda s: asyncio.sleep(0))
            bad.request(MY)
            await asyncio.gather(*list(bad._inflight.values()))

        asyncio.run(go())
        self.assertEqual((clf.stats["rpc_timeouts"], clf.stats["rpc_errors"], clf.stats["rpc_gave_up"]), (1, 2, 2))
        self.assertEqual((clf.lookup(MX), clf.lookup(MY)), ((None, None), (None, None)))

    def test_an_earlier_ws_class_stops_the_lookup_before_any_rpc_call(self):
        clf = h5.SynClassifier()
        clf.record(MX, False, "ws")
        made = []

        async def go():
            fb = h5.RpcFallback(clf, client_factory=lambda: made.append(1) or FakeRpc([], {}), delays=(0.0,), sleep=lambda s: asyncio.sleep(0))
            fb.request(MX)
            await asyncio.gather(*list(fb._inflight.values()))

        asyncio.run(go())
        self.assertEqual(made, [])

    def test_inflight_is_capped(self):
        clf = h5.SynClassifier()

        async def go():
            fb = h5.RpcFallback(clf, client_factory=lambda: FakeRpc([], {}, delay=0.1), delays=(0.0,), max_inflight=2, sleep=lambda s: asyncio.sleep(0))
            got = [fb.request(FX[i]["mint"]) for i in range(4)]
            await fb.close()
            return got

        self.assertEqual(asyncio.run(go()), [True, True, False, False])
        self.assertEqual(clf.stats["rpc_dropped"], 2)

    def test_rpc_url_guard(self):
        self.assertEqual(h5.check_rpc_url("https://api.mainnet-beta.solana.com"), "https://api.mainnet-beta.solana.com")
        for bad in ("https://mainnet.helius-rpc.com/?api-key=abc", "https://api.mainnet-beta.solana.com/?k=1", "http://x.example", "https://u:p@x.example"):
            with self.assertRaises(ValueError):
                h5.check_rpc_url(bad)


class EngineGateTests(unittest.TestCase):
    def run_scenario(self, clf, **kw):
        eng, out = make_engine(classifier=clf, **kw)
        t = scenario(eng)
        run(eng, t)
        eng.close_all("t")
        return eng, out

    def assert_nothing_about_a_paper_result(self, out):
        for typ in ("trigger", "outcome", "strip", "skipped_no_sps"):
            self.assertEqual(types(out, typ), [], typ)

    def test_synthetic_pool_is_excluded_with_no_trigger_outcome_or_strip(self):
        eng, out = self.run_scenario(h5.StaticClassifier(True, "ws"))
        self.assert_nothing_about_a_paper_result(out)
        (ex,) = types(out, "excluded")
        self.assertEqual(set(ex), EXCLUDED_KEYS)  # no Q, price, V or outcome field
        self.assertEqual((ex["reason"], ex["pool"], ex["mint"], ex["s0"]), ("synthetic", POOL, MINT, 1000))
        self.assertEqual(ex["slot"], 1110)
        self.assertEqual((eng.counters["excluded_synthetic"], eng.counters["excluded_unclassified"]), (1, 0))
        self.assertEqual(eng.counters["triggers_pv"] + eng.counters["triggers_fv"] + eng.counters["outcomes"] + eng.counters["strips"], 0)
        for p in eng.pools.values():  # nothing pending for the pool, closed or not
            self.assertEqual(p.pending, [])
        (pool,) = types(out, "pool")
        self.assertEqual((pool["synthetic"], pool["synthetic_src"], pool["triggered"]), (True, "ws", []))

    def test_unclassified_pool_is_excluded_and_so_is_the_default_engine(self):
        for clf in (h5.SynClassifier(), None):
            eng, out = self.run_scenario(clf)
            self.assert_nothing_about_a_paper_result(out)
            (ex,) = types(out, "excluded")
            self.assertEqual((ex["reason"], set(ex)), ("unclassified", EXCLUDED_KEYS))
            self.assertEqual((eng.counters["excluded_synthetic"], eng.counters["excluded_unclassified"]), (0, 1))
            (pool,) = types(out, "pool")
            self.assertEqual((pool["synthetic"], pool["synthetic_src"]), (None, None))

    def test_non_synthetic_pool_writes_the_same_triggers_as_before_plus_the_two_fields(self):
        eng, out = self.run_scenario(h5.StaticClassifier(False, "ws"))
        self.assertEqual(types(out, "excluded"), [])
        trig = types(out, "trigger")
        self.assertEqual(sorted(r["variant"] for r in trig), ["fv", "pv"])
        self.assertTrue(all(r["synthetic"] is False and r["synthetic_src"] == "ws" for r in trig))
        self.assertGreater(len(types(out, "outcome")), 0)  # the paper outcome machinery runs for a non-synthetic pool
        self.assertEqual(len(types(out, "strip")), 1)
        self.assertEqual((eng.counters["excluded_synthetic"], eng.counters["excluded_unclassified"]), (0, 0))
        (pool,) = types(out, "pool")
        self.assertEqual((pool["synthetic"], pool["synthetic_src"], pool["triggered"]), (False, "ws", ["fv", "pv"]))
        # the rest of every record does not depend on where the class came from
        _, out2 = self.run_scenario(h5.StaticClassifier(False, "rpc"))
        strip = lambda rs: [{k: v for k, v in r.items() if k != "synthetic_src"} for r in rs if r["type"] in ("trigger", "outcome", "strip", "pool")]
        self.assertEqual(strip(out), strip(out2))

    def test_a_class_that_arrives_before_the_trigger_is_used(self):
        clf = h5.SynClassifier()
        eng, out = make_engine(classifier=clf)
        t = scenario(eng)
        for r in t.rows[:-2]:  # up to the BOOST buys: the pool is open and unclassified
            eng.on_trade(r)
        self.assertEqual(types(out, "excluded"), [])
        clf.record(MINT, False, "rpc")
        for r in t.rows[-2:]:
            eng.on_trade(r)
        eng.close_all("t")
        self.assertEqual(types(out, "excluded"), [])
        self.assertEqual({r["synthetic_src"] for r in types(out, "trigger")}, {"rpc"})

    def test_a_class_that_arrives_after_the_decision_does_not_revive_it(self):
        clf = h5.SynClassifier()
        eng, out = make_engine(classifier=clf)
        t = scenario(eng)
        for r in t.rows[:-1]:  # through the drain sell: decided while unclassified
            eng.on_trade(r)
        (ex,) = types(out, "excluded")
        clf.record(MINT, False, "rpc")
        p = eng.pools[POOL]
        p.trig.pop("fv")  # as if the other variant had not fired yet
        eng._fire(p, p.prints[-1], len(p.prints) - 1, "fv", SPS, 1.0, 0, None, "x")
        eng.on_trade(t.rows[-1])
        eng.close_all("t")
        self.assertEqual(types(out, "excluded"), [ex])  # still one record per pool
        self.assertEqual(types(out, "trigger") + types(out, "outcome") + types(out, "strip"), [])
        (pool,) = types(out, "pool")
        self.assertEqual((pool["synthetic"], pool["synthetic_src"], pool["triggered"]), (False, "rpc", []))  # the close record states the class as known by then

    def test_the_rpc_request_goes_out_once_for_a_pool_unclassified_at_s0_and_never_for_a_classified_one(self):
        asked = []
        eng, out = make_engine(classifier=h5.SynClassifier())
        eng.syn_request = lambda mint, sig=None: asked.append(mint)
        t = scenario(eng)
        run(eng, t)
        self.assertEqual(asked, [MINT])
        asked2 = []
        eng2, _ = make_engine(classifier=h5.StaticClassifier(True, "ws"))
        eng2.syn_request = lambda mint, sig=None: asked2.append(mint)
        run(eng2, scenario(eng2))
        self.assertEqual(asked2, [])

    def test_a_failing_rpc_request_hook_cannot_stop_the_feed(self):
        def boom(mint, sig=None):
            raise RuntimeError("no loop")

        eng, out = make_engine(classifier=h5.SynClassifier())
        eng.syn_request = boom
        run(eng, scenario(eng))
        eng.close_all("t")
        self.assertEqual(eng.counters["syn_request_errors"], 1)
        self.assertEqual(len(types(out, "excluded")), 1)

    def test_cap_pick_sealed_pool_is_byte_for_byte_unchanged_no_excluded_record_no_counter_no_request(self):
        asked = []
        results = []
        for clf in (h5.StaticClassifier(True, "ws"), h5.StaticClassifier(False, "ws"), h5.SynClassifier()):
            eng, out = make_engine(classifier=clf, seal_start_ms=1_800_000_000 * 1000, suppress_outcome=lambda m: True)
            eng.syn_request = lambda mint, sig=None: asked.append(mint)
            run(eng, scenario(eng))
            eng.close_all("t")
            self.assertEqual(types(out, "excluded"), [])
            self.assertEqual((eng.counters["excluded_synthetic"], eng.counters["excluded_unclassified"]), (0, 0))
            (pool,) = types(out, "pool")
            self.assertNotIn("synthetic", pool)  # the sealed record keeps its open-time keys only
            results.append([{k: v for k, v in r.items() if k != "t_ms"} for r in out if r["type"] in ("pool", "hb")])
        self.assertEqual(asked, [])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])  # the class does not change a sealed pool's records at all

    def test_look2_window_pool_gets_the_excluded_record_but_no_outcome_either_way(self):
        ts0 = 1_800_000_000
        for clf, expect in ((h5.StaticClassifier(True, "ws"), 1), (h5.StaticClassifier(False, "ws"), 0)):
            eng, out = make_engine(classifier=clf, h5_look2_start_ms=ts0 * 1000)
            run(eng, scenario(eng))
            eng.close_all("t")
            self.assertEqual(len(types(out, "excluded")), expect)
            self.assertEqual(types(out, "outcome"), [])
            self.assertEqual(len(types(out, "trigger")), 0 if expect else 2)


class CreatePoolNoticeTests(unittest.TestCase):
    """The migrate tx reaches the shadow as the PumpSwap CreatePool notice (decode_notice): its logs are tested directly, no RPC."""

    @staticmethod
    def mint_of(note):
        eng, _ = make_engine(classifier=h5.SynClassifier())
        h5.decode_notice(eng, note, {})
        return eng.announced[next(iter(eng.announced))][2]

    def run_pool(self, note, clf):
        eng, out = make_engine(classifier=clf)
        h5.decode_notice(eng, note, {})
        (pool,) = eng.announced
        mint = eng.announced[pool][2]
        s0 = note.slot + 1
        t = Tape(pool=pool, slot0=s0)
        t.row(s0, "buy", "A", SOL // 10)
        boost_buys(t, s0)
        drain(t, s0 + 110, 35.0)
        t.row(s0 + 200, "buy", "Z", SOL // 20)
        run(eng, t)
        eng.close_all("t")
        return eng, out, mint

    def test_a_real_migrate_notice_has_cut_logs_so_the_websocket_cannot_call_it_plain(self):
        note = fixture_notice("create_pool_init_boost.json")
        self.assertIn("Log truncated", note.logs)  # recorded 2026-10-07: the CreatePool line is the only event line left
        clf = h5.SynClassifier()
        mint = self.mint_of(note)
        clf.record_part(mint, "comp", False, "ws")  # the pump.fun socket delivered a clean CompleteEvent tx earlier
        asked = []
        eng, out = make_engine(classifier=clf)
        eng.syn_request = lambda m, sig=None: asked.append((m, sig))
        h5.decode_notice(eng, note, {})
        self.assertEqual(asked, [(mint, note.signature)])  # asked at the announcement, with the migrate tx's signature
        self.assertEqual(clf.lookup(mint), (None, None))  # the websocket could not read the cut part: still unclassified
        self.assertEqual(clf.stats["ws_truncated"], 1)

    def test_comp_from_ws_and_mig_from_rpc_make_the_pool_plain_and_it_triggers(self):
        note = fixture_notice("create_pool_init_boost.json")
        clf = h5.SynClassifier()
        mint = self.mint_of(note)
        clf.record_part(mint, "comp", False, "ws")
        clf.record_part(mint, "mig", False, "rpc")  # RpcFallback read the migrate tx's inner instructions
        eng, out, m = self.run_pool(note, clf)
        self.assertEqual(m, mint)
        self.assertEqual(types(out, "excluded"), [])
        self.assertEqual({(r["synthetic"], r["synthetic_src"]) for r in types(out, "trigger")}, {(False, "rpc")})

    def test_event_only_in_the_migrate_tx_gives_synthetic_and_no_trigger(self):
        base = fixture_notice("create_pool_init_boost.json")
        note = base._replace(logs=base.logs + (_pcb_line(),))
        clf = h5.SynClassifier()
        mint = self.mint_of(base)
        clf.record_part(mint, "comp", False, "ws")  # the completing tx is clean
        eng, out, _ = self.run_pool(note, clf)
        self.assertEqual(clf.lookup(mint), (True, "ws"))
        self.assertEqual(types(out, "trigger") + types(out, "outcome") + types(out, "strip"), [])
        (ex,) = types(out, "excluded")
        self.assertEqual((ex["reason"], set(ex)), ("synthetic", EXCLUDED_KEYS))
        self.assertEqual(eng.counters["excluded_synthetic"], 1)

    def test_a_clean_migrate_tx_with_the_completing_tx_unread_is_unclassified(self):
        note = fixture_notice("create_pool_init_boost.json")
        eng, out, mint = self.run_pool(note, h5.SynClassifier())
        self.assertEqual(types(out, "trigger"), [])
        (ex,) = types(out, "excluded")
        self.assertEqual(ex["reason"], "unclassified")
        self.assertEqual(eng.counters["excluded_unclassified"], 1)


class ReplayClassifierTests(unittest.TestCase):
    def test_pre_event_classifier_is_false_below_the_pinned_slot_and_unknown_at_or_above_it(self):
        c = h5.PreEventClassifier()
        self.assertEqual(c.lookup("m", h5.PCB_FIRST_DEPLOY_SLOT - 1), (False, "pre_event_binary"))
        self.assertEqual(c.lookup("m", 445_000_000), (False, "pre_event_binary"))
        self.assertEqual(c.lookup("m", h5.PCB_FIRST_DEPLOY_SLOT), (None, None))  # fail closed
        self.assertEqual(c.lookup("m", None), (None, None))

    def test_the_pinned_slot_is_a_pump_deploy_after_the_last_replayable_hour(self):
        pins = json.loads((Path(__file__).parent / "pump_structure_pins.json").read_text())
        self.assertIn("452654932", json.dumps(pins))  # the 2026-10-02T15:47Z deploy: still without the event
        self.assertGreater(h5.PCB_FIRST_DEPLOY_SLOT, 452_654_932)
        self.assertLess(min(t["slot"] for t in SYN), 10**9)
        self.assertLessEqual(h5.PCB_FIRST_DEPLOY_SLOT, min(t["slot"] for t in SYN))  # every recorded synthetic completion is at or after it

    def test_engine_with_the_replay_classifier_writes_triggers_tagged_pre_event_binary(self):
        eng, out = make_engine(classifier=h5.PreEventClassifier())
        run(eng, scenario(eng))
        eng.close_all("t")
        trig = types(out, "trigger")
        self.assertEqual(sorted(r["variant"] for r in trig), ["fv", "pv"])
        self.assertEqual({(r["synthetic"], r["synthetic_src"]) for r in trig}, {(False, "pre_event_binary")})
        self.assertEqual(types(out, "excluded"), [])

    def test_the_replay_entry_point_uses_it(self):
        import inspect

        self.assertIn("classifier=PreEventClassifier()", inspect.getsource(h5.run_replay))


if __name__ == "__main__":
    unittest.main()
