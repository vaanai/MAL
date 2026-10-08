"""Schema and decode tests for the history loader. No network."""

from __future__ import annotations

import base64
import json
import os
import random
import shutil
import signal
import tempfile
from datetime import datetime, timezone
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.pump_history_backfill as backfill_mod
from observe.trade_decode import WSOL_MINT, b58encode, records_from_logs
from tools.backfill_verify import MAX_SLOTS_PER_HOUR
from tools.pump_history_backfill import (
    BUDGET_CODE,
    SOURCE,
    CreditBudget,
    JsonlSink,
    RateLimiter,
    backfill_trade_row,
    budget_bytes,
    compare_trades,
    consume_slots,
    decode_complete_event,
    decode_create_event,
    decode_migration_event,
    empty_checkpoint,
    helius_http_url,
    hour_key,
    iter_jsonl,
    lifecycle_from_logs,
    live_tape_hour_sealed,
    main,
    parse_credit_headers,
    plan_hours,
    projected_backfill_days,
    redact_rpc_url,
    resolve_rpc_url,
    resolve_unresolved,
    rows_from_block,
    run_hour,
    slot_for_time,
    hour_end_with_gap,
    skip_hour_for_live_tape,
)

FIXTURES = Path(__file__).resolve().parent.parent / "observe" / "fixtures"
SLOT = 450276100
SIG = "sig111111111111111111111111111111111111111111111111111111111111111111111111111111"
BLOCK_TIME = 1790318932


def _line(name: str) -> str:
    blob = (FIXTURES / name).read_text(encoding="utf-8").strip()
    return f"Program data: {blob}"


def _borsh_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return len(raw).to_bytes(4, "little") + raw


class BudgetTests(unittest.TestCase):
    def test_cap_and_headroom(self) -> None:
        total = 157_396_975_616
        avail = 147_819_425_792
        self.assertEqual(budget_bytes(total, avail), 40 * 1024**3)
        # Almost full: only the slack above 20% is usable, and it is under the cap.
        tight = budget_bytes(1000, 250, cap_bytes=10_000)
        self.assertEqual(tight, 50)
        self.assertEqual(budget_bytes(1000, 100, cap_bytes=10_000), 0)


class CreateAndMigrationTests(unittest.TestCase):
    def test_create_event_prefix(self) -> None:
        mint = bytes(range(32))
        user = bytes(range(32, 64))
        creator = bytes([7] * 32)
        curve = bytes([9] * 32)
        raw = b"".join(
            [
                bytes.fromhex("1b72a94ddeeb6376"),
                _borsh_str("Name"),
                _borsh_str("SYM"),
                _borsh_str("https://example.invalid/u"),
                mint,
                curve,
                user,
                creator,
                (1790318932).to_bytes(8, "little", signed=True),
                (1_000_000).to_bytes(8, "little"),
                (30_000_000_000).to_bytes(8, "little"),
                (800_000).to_bytes(8, "little"),
                (1_000_000_000_000_000).to_bytes(8, "little"),
            ]
        )
        ev = decode_create_event(raw)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev["mint"], b58encode(mint))
        self.assertEqual(ev["trader"], b58encode(user))
        self.assertEqual(ev["creator"], b58encode(creator))
        self.assertEqual(ev["event_ts"], 1790318932)
        self.assertEqual(ev["quote_reserve"], 30_000_000_000)
        self.assertEqual(ev["quote_mint"], WSOL_MINT)
        self.assertFalse(ev["is_mayhem_mode"])
        # Native SOL is the zero pubkey on current CreateEvent tails.
        usdc = bytes([4]) * 32
        with_tail = raw + bytes(32) + bytes([1, 0]) + bytes(32)
        ev2 = decode_create_event(with_tail)
        assert ev2 is not None
        self.assertTrue(ev2["is_mayhem_mode"])
        self.assertEqual(ev2["quote_mint"], WSOL_MINT)
        quoted = raw + bytes(32) + bytes([0, 0]) + usdc
        ev3 = decode_create_event(quoted)
        assert ev3 is not None
        self.assertEqual(ev3["quote_mint"], b58encode(usdc))

    def test_migration_and_complete_layouts(self) -> None:
        def pk(n: int) -> bytes:
            return bytes([n]) * 32

        mig = b"".join(
            [
                bytes.fromhex("bde95db95c94ea94"),
                pk(1),
                pk(2),
                (50).to_bytes(8, "little"),
                (2_000_000_000).to_bytes(8, "little"),
                (100).to_bytes(8, "little"),
                pk(3),
                (1790319000).to_bytes(8, "little", signed=True),
                pk(4),
                pk(5),
            ]
        )
        ev = decode_migration_event(mig)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev["type"], "migration")
        self.assertEqual(ev["mint"], b58encode(pk(2)))
        self.assertEqual(ev["trader"], b58encode(pk(1)))
        self.assertEqual(ev["sol_lamports"], 2_000_000_000)
        self.assertEqual(ev["token_raw"], 50)
        self.assertEqual(ev["pool"], b58encode(pk(4)))
        self.assertEqual(ev["event_ts"], 1790319000)

        comp = b"".join(
            [
                bytes.fromhex("5f72619cd42e9808"),
                pk(1),
                pk(2),
                pk(3),
                (1790319001).to_bytes(8, "little", signed=True),
                pk(5),
            ]
        )
        done = decode_complete_event(comp)
        self.assertIsNotNone(done)
        assert done is not None
        self.assertEqual(done["type"], "complete")
        self.assertEqual(done["mint"], b58encode(pk(2)))
        native = b"".join(
            [
                bytes.fromhex("5f72619cd42e9808"),
                pk(1),
                pk(2),
                pk(3),
                (1790319001).to_bytes(8, "little", signed=True),
                bytes(32),
            ]
        )
        native_ev = decode_complete_event(native)
        assert native_ev is not None
        self.assertEqual(native_ev["quote_mint"], WSOL_MINT)
        found_c, found_m = lifecycle_from_logs(
            [
                "Program data: " + base64.b64encode(mig).decode(),
                "Program data: " + base64.b64encode(comp).decode(),
            ]
        )
        self.assertEqual(found_c, [])
        self.assertEqual([row["type"] for row in found_m], ["migration", "complete"])


class TapeSchemaTests(unittest.TestCase):
    def test_bonding_row_nulls_receive_time(self) -> None:
        decoded = records_from_logs(
            [_line("pump_trade_event.b64")],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=0,
            commitment="confirmed",
            feed="public_rpc_getblock",
        )
        row = backfill_trade_row(decoded[0], BLOCK_TIME)
        self.assertEqual(row["source"], SOURCE)
        self.assertIsNone(row["t_recv"])
        self.assertIsNone(row["t_recv_ms"])
        self.assertEqual(row["block_time"], BLOCK_TIME)
        self.assertEqual(row["v"], 1)
        self.assertEqual(row["venue"], "pump_bonding")
        self.assertEqual(row["sol_lamports"], 1000)
        self.assertEqual(row["token_raw"], 12728720)
        self.assertEqual(row["slot"], SLOT)
        self.assertEqual(row["event_ts"], 1790318932)
        self.assertEqual(row["feed"], "public_rpc_getblock")

    def test_block_decode_and_pumpswap_lookup(self) -> None:
        block = {
            "slot": SLOT,
            "blockTime": BLOCK_TIME,
            "transactions": [
                {
                    "meta": {"err": None, "logMessages": [_line("pump_trade_event.b64")]},
                    "transaction": {"signatures": [SIG]},
                },
                {
                    "meta": {"err": {"InstructionError": [0, "Custom"]}, "logMessages": [_line("pump_trade_event.b64")]},
                    "transaction": {"signatures": ["failed"]},
                },
            ],
        }
        out = rows_from_block(block, {})
        self.assertEqual(len(out["trades"]), 1)
        self.assertEqual(out["trades"][0]["signature"], SIG)
        self.assertEqual(out["trades"][0]["tx_index"], 0)
        self.assertIsNone(out["trades"][0]["t_recv_ms"])
        pending = records_from_logs(
            [_line("pumpswap_sell_event.b64")],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=0,
            commitment="confirmed",
            feed="public_rpc_getblock",
        )
        self.assertIsNone(pending[0]["mint"])

        def lookup(pools: list[str]) -> dict[str, tuple[str, str]]:
            self.assertEqual(len(pools), 1)
            return {pools[0]: ("Mint111", WSOL_MINT)}

        ready, dropped = resolve_unresolved(pending, {}, BLOCK_TIME, lookup)
        self.assertEqual(dropped, 0)
        self.assertEqual(ready[0]["mint"], "Mint111")
        self.assertEqual(ready[0]["quote_is_wsol"], True)
        self.assertEqual(ready[0]["source"], SOURCE)
        self.assertIsNone(ready[0]["t_recv_ms"])
        self.assertEqual(ready[0]["v"], 2)

    def test_compare_matches_amounts(self) -> None:
        live = [
            {
                "signature": "a",
                "event_index": 0,
                "venue": "pump_bonding",
                "mint": "m",
                "trader": "t",
                "side": "buy",
                "sol_lamports": 5,
                "token_raw": 9,
                "slot": 1,
                "event_ts": 2,
                "quote_reserve": 3,
                "base_reserve": 4,
            }
        ]
        back = [dict(live[0]), dict(live[0], signature="b", zero_sol=True, sol_lamports=0)]
        report = compare_trades(live, back)
        self.assertEqual(report["match"], 1)
        self.assertEqual(report["only_backfill"], 1)
        self.assertEqual(report["only_backfill_zero_sol"], 1)
        self.assertEqual(report["only_live"], 0)


class HeliusPrepTests(unittest.TestCase):
    def test_url_from_key_is_redacted(self) -> None:
        url = helius_http_url("unit-test-key")
        self.assertTrue(url.startswith("https://mainnet.helius-rpc.com/?api-key="))
        self.assertIn("unit-test-key", url)
        self.assertNotIn("unit-test-key", redact_rpc_url(url))
        self.assertNotIn("unit-test-key", redact_rpc_url(f"failed {url}"))
        with self.assertRaises(ValueError):
            helius_http_url("bad key")
        public, kind = resolve_rpc_url(None, {})
        self.assertEqual(kind, "public")
        self.assertNotIn("api-key", public)
        helius, kind = resolve_rpc_url(None, {"HELIUS_API_KEY": "unit-test-key"})
        self.assertEqual(kind, "helius")
        overridden, kind = resolve_rpc_url("https://api.mainnet-beta.solana.com", {"HELIUS_API_KEY": "unit-test-key"})
        self.assertEqual(kind, "public")
        self.assertNotIn("unit-test-key", overridden)

    def test_bulk_helius_stops_before_any_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HELIUS_API_KEY": "unit-test-key"}, clear=False):
                code = main(["--until", "2026-09-25T07:00:00Z", "--hours", "1", "--out", tmp])
        self.assertEqual(code, 2)

    def test_credit_cap_and_projection(self) -> None:
        budget = CreditBudget(3, 1)
        self.assertTrue(budget.reserve())
        self.assertTrue(budget.reserve())
        self.assertTrue(budget.reserve())
        self.assertFalse(budget.reserve())
        self.assertEqual(budget.used, 3)
        public = CreditBudget(7_000_000, 0, used=10)
        self.assertTrue(public.can_afford())
        self.assertTrue(public.reserve())
        self.assertEqual(public.used, 10)
        proj = projected_backfill_days(7_000_000, 1, 13_527 * 24, 143_478_622 * 24, 40 * 1024**3)
        self.assertEqual(proj["binding"], "disk")
        self.assertGreater(proj["days_by_credits"], proj["days_by_disk"])
        costly = projected_backfill_days(7_000_000, 10, 13_527 * 24, 143_478_622 * 24, 40 * 1024**3)
        self.assertEqual(costly["binding"], "credits")
        self.assertLess(costly["days"], 3)
        self.assertEqual(parse_credit_headers([{"name": "x-credits", "value": "1"}, {"name": "x-credits", "value": "1"}]), 1)
        self.assertIsNone(parse_credit_headers([{"name": "x-credits", "value": "1"}, {"name": "x-credits", "value": "10"}]))

    def test_hours_are_newest_first(self) -> None:
        hours = plan_hours(1_790_323_200, 3)
        self.assertEqual(len(hours), 3)
        self.assertEqual(hours[0][1] - hours[0][0], 3600)
        self.assertGreater(hours[0][0], hours[1][0])
        self.assertGreater(hours[1][0], hours[2][0])

    def test_resume_truncates_partial_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            sink = JsonlSink(path)
            sink.write({"a": 1})
            sink.write({"a": 2})
            offset = sink.offset()
            sink.write({"a": 3})
            sink.close(seal=False)
            resumed = JsonlSink(path, resume_bytes=offset)
            resumed.write({"a": 4})
            resumed.close(seal=False)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows, [{"a": 1}, {"a": 2}, {"a": 4}])

    def test_concurrent_fetch_stays_ordered_and_stops_on_budget(self) -> None:
        current = 0
        max_inflight = 0
        lock = threading.Lock()

        def fetch(slot: int) -> tuple[dict, int, None]:
            nonlocal current, max_inflight
            with lock:
                current += 1
                max_inflight = max(max_inflight, current)
            time.sleep(0.05)
            with lock:
                current -= 1
            return {"slot": slot, "blockTime": 1, "transactions": []}, 1, None

        consumed: list[int] = []
        count, reason = consume_slots([1, 2, 3, 4], 3, fetch, lambda block, _w, _c: consumed.append(block["slot"]))
        self.assertEqual(consumed, [1, 2, 3, 4])
        self.assertIsNone(reason)
        self.assertGreater(max_inflight, 1)

        budget = CreditBudget(2, 1)

        def limited(slot: int) -> tuple[dict | None, int, int | None]:
            if not budget.reserve():
                return None, 0, BUDGET_CODE
            return {"slot": slot}, 1, None

        got: list[int] = []
        count, reason = consume_slots(
            [10, 11, 12, 13],
            2,
            limited,
            lambda block, _w, _c: got.append(block["slot"]),
            stop_before=lambda: None if budget.can_afford() else "credit",
        )
        self.assertEqual(reason, "credit")
        self.assertLess(count, 4)
        self.assertEqual(got, [10, 11])


class LiveTapeSkipTests(unittest.TestCase):
    def test_sealed_hourly_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = Path(tmp)
            key = "2026-09-25T16"
            (tape / f"trades-{key}.jsonl.zst").write_text("x", encoding="utf-8")
            self.assertTrue(live_tape_hour_sealed(tape, key))
            start = int(datetime.fromisoformat("2026-09-25T16:00:00+00:00").timestamp())
            end = start + 3600
            self.assertTrue(skip_hour_for_live_tape(tape, start, end, set()))

    def test_proof_hour_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = Path(tmp)
            key = "2026-09-25T07"
            (tape / f"trades-{key}.jsonl.zst").write_text("x", encoding="utf-8")
            start = int(datetime.fromisoformat("2026-09-25T07:00:00+00:00").timestamp())
            end = start + 3600
            self.assertFalse(skip_hour_for_live_tape(tape, start, end, {key}))

    def test_gap_hour_not_skipped_when_live_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = Path(tmp)
            key = "2026-09-25T06"
            (tape / f"trades-{key}.jsonl.zst").write_text("x", encoding="utf-8")
            start = int(datetime.fromisoformat("2026-09-25T06:00:00+00:00").timestamp())
            end = start + 3600
            gap = int(datetime.fromisoformat("2026-09-25T06:58:00+00:00").timestamp())
            self.assertEqual(hour_end_with_gap(start, end, gap), gap)
            self.assertFalse(skip_hour_for_live_tape(tape, start, end, set(), gap))


class SlotForTimeSkipRobustnessTests(unittest.TestCase):
    """Reproduces the backwards start_slot > end_slot bug (2026-09-11T03)."""

    ANCHOR_SLOT = 450278777
    ANCHOR_TIME = 1790319576
    SLOT_SEC = 0.4

    def _install_fake_clock(self, skip_set: set[int]) -> None:
        def fake(url: str, slot: int, limiter: RateLimiter, budget=None):
            if slot in skip_set:
                return None
            return int(self.ANCHOR_TIME + (slot - self.ANCHOR_SLOT) * self.SLOT_SEC)

        self.addCleanup(setattr, backfill_mod, "block_time_of", backfill_mod.block_time_of)
        backfill_mod.block_time_of = fake

    def test_monotonic_across_an_hour_with_skipped_slots(self) -> None:
        random.seed(3)
        lo_range = self.ANCHOR_SLOT - 6_000_000
        hi_range = self.ANCHOR_SLOT + 100
        skip_set = {s for s in range(lo_range, hi_range) if random.random() < 0.15}
        self._install_fake_clock(skip_set)
        limiter = RateLimiter(1000)
        start_ts = int(datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime(2026, 9, 11, 4, 0, tzinfo=timezone.utc).timestamp())
        start_slot = slot_for_time(
            "http://x", start_ts, self.ANCHOR_SLOT, self.ANCHOR_TIME, limiter
        )
        end_slot = slot_for_time("http://x", end_ts, self.ANCHOR_SLOT, self.ANCHOR_TIME, limiter)
        self.assertLess(
            start_slot,
            end_slot,
            "an hour's end must resolve to a later slot than its start",
        )
        span = end_slot - start_slot
        # ~0.4s/slot -> ~9000 slots/hour on this synthetic clock.
        self.assertGreater(span, 5000)
        self.assertLess(span, 20000)

    def test_backward_target_is_not_clamped_to_anchor(self) -> None:
        # Before the fix, span = max(target - anchor_time, 0) forced every
        # backward-walker target to guess = anchor_slot regardless of how
        # far in the past it was.
        self._install_fake_clock(set())
        limiter = RateLimiter(1000)
        target = self.ANCHOR_TIME - 10 * 86400
        got = slot_for_time("http://x", target, self.ANCHOR_SLOT, self.ANCHOR_TIME, limiter)
        # Exact answer on this linear synthetic clock (SLOT_SEC per slot).
        expected = self.ANCHOR_SLOT + int((target - self.ANCHOR_TIME) / self.SLOT_SEC)
        self.assertLess(abs(got - expected), 5)


def _hour_bounds(key: str) -> tuple[int, int]:
    start = int(datetime.fromisoformat(f"{key}:00:00+00:00").timestamp())
    return start, start + 3600


class _TimeBoundedTestCase(unittest.TestCase):
    """Fails fast instead of hanging when a regression falls back to real retries.

    A buggy resume/self-heal path can end up calling the real, retrying
    fetch_block against a fake URL, which sleeps through several minutes of
    backoff per slot. SIGALRM turns that hang into a prompt, readable
    failure instead of a CI timeout.
    """

    TIME_LIMIT_S = 10

    def setUp(self) -> None:
        super().setUp()
        if hasattr(signal, "SIGALRM"):
            self._prev_handler = signal.signal(signal.SIGALRM, self._on_alarm)
            signal.alarm(self.TIME_LIMIT_S)
        else:
            self._prev_handler = None

    def tearDown(self) -> None:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self._prev_handler)
        super().tearDown()

    def _on_alarm(self, signum, frame) -> None:
        raise AssertionError(
            f"test exceeded {self.TIME_LIMIT_S}s -- likely a hang from an "
            f"unguarded resume path retrying real network calls"
        )


class RunHourGuardTests(_TimeBoundedTestCase):
    """The three "refuse to seal" invariants tools/pump_history_backfill.py must hold."""

    def setUp(self) -> None:
        super().setUp()
        self._orig_slots_between = backfill_mod.slots_between
        self.addCleanup(setattr, backfill_mod, "slots_between", self._orig_slots_between)

    def _common_kwargs(self, tmp: str) -> dict:
        limiter = RateLimiter(1000)
        return dict(
            url="http://x",
            out_dir=Path(tmp),
            limiter=limiter,
            lookup_limiter=limiter,
            pool_mints={},
            workers=1,
            max_bytes=10**9,
            anchor_slot=450278777,
            anchor_time=1790319576,
            budget=CreditBudget(10**9, 0),
        )

    def test_backwards_range_refused(self) -> None:
        backfill_mod.slots_between = lambda *a, **k: []
        start_ts, end_ts = _hour_bounds("2026-09-11T03")
        with tempfile.TemporaryDirectory() as tmp:
            summary = run_hour(
                start_ts=start_ts,
                end_ts=end_ts,
                slot_start=446300168,
                slot_end=446060631,
                **self._common_kwargs(tmp),
            )
            self.assertTrue(summary["skipped"])
            self.assertEqual(summary["stop_reason"], "bad_slot_range")
            self.assertFalse((Path(tmp) / "trades").exists())
            self.assertFalse((Path(tmp) / f"stats-2026-09-11T03.json").exists())

    def test_no_located_slots_refused(self) -> None:
        backfill_mod.slots_between = lambda *a, **k: []
        start_ts, end_ts = _hour_bounds("2026-09-11T05")
        with tempfile.TemporaryDirectory() as tmp:
            summary = run_hour(
                start_ts=start_ts,
                end_ts=end_ts,
                slot_start=100,
                slot_end=9500,
                **self._common_kwargs(tmp),
            )
            self.assertTrue(summary["skipped"])
            self.assertEqual(summary["stop_reason"], "bad_slot_range")

    def test_wild_span_refused(self) -> None:
        backfill_mod.slots_between = lambda *a, **k: list(range(0, 50))
        start_ts, end_ts = _hour_bounds("2026-09-11T06")
        with tempfile.TemporaryDirectory() as tmp:
            summary = run_hour(
                start_ts=start_ts,
                end_ts=end_ts,
                slot_start=0,
                slot_end=50,
                **self._common_kwargs(tmp),
            )
            self.assertTrue(summary["skipped"])
            self.assertEqual(summary["stop_reason"], "bad_slot_span")
            self.assertFalse((Path(tmp) / "trades").exists())

    # --- default slot-span bounds (SIMD-0525: 200 ms slots from epoch 1053) ----------
    # These run run_hour WITHOUT min/max kwargs, so they exercise the shipped defaults.

    def _run_default_bounds(self, span: int, hour: str) -> tuple[dict, Path]:
        backfill_mod.slots_between = lambda *a, **k: list(range(0, span))

        def fake_fetch_block(url, slot, limiter, budget=None, header_out=None, *, full=True):
            return {"slot": slot, "blockTime": 0, "transactions": []}, 1, None

        orig_fetch = backfill_mod.fetch_block
        backfill_mod.fetch_block = fake_fetch_block
        self.addCleanup(setattr, backfill_mod, "fetch_block", orig_fetch)
        start_ts, end_ts = _hour_bounds(hour)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        summary = run_hour(start_ts=start_ts, end_ts=end_ts, slot_start=0, slot_end=span, **self._common_kwargs(tmp))
        return summary, Path(tmp)

    def test_default_upper_bound_is_the_shared_constant(self) -> None:
        self.assertIs(backfill_mod.DEFAULT_MAX_SLOTS_PER_HOUR, MAX_SLOTS_PER_HOUR)
        self.assertEqual(MAX_SLOTS_PER_HOUR, 19_500)
        self.assertEqual(backfill_mod.DEFAULT_MIN_SLOTS_PER_HOUR, 10_500)

    def test_cli_defaults_are_the_shared_bounds(self) -> None:
        seen: list[dict] = []

        def fake_run_hour(**kw):
            seen.append(kw)
            return {"hour": "x", "skipped": True, "stop_reason": "test"}  # a skipped hour stops the loop

        env = {k: v for k, v in os.environ.items() if k != "HELIUS_API_KEY"}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=True), \
                patch.object(backfill_mod, "run_hour", fake_run_hour), \
                patch.object(backfill_mod, "skip_hour_for_live_tape", lambda *a, **k: False):
            code = main(["--until", "2026-10-09T15:00:00Z", "--hours", "1", "--out", tmp, "--rpc", "http://x"])
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["min_slots_per_hour"], 10_500)
        self.assertEqual(seen[0]["max_slots_per_hour"], MAX_SLOTS_PER_HOUR)

    def test_18000_slot_hour_seals_with_default_bounds(self) -> None:
        # ~200 ms slots: 3600 s / 0.2 s = 18,000 slots in the hour.
        key = "2026-10-10T03"
        summary, out = self._run_default_bounds(18_000, key)
        self.assertIsNone(summary.get("stop_reason"))
        self.assertFalse(summary.get("skipped"))
        self.assertEqual(summary["slots_done"], 18_000)
        self.assertEqual(summary["end_slot"] - summary["start_slot"], 18_000)
        self.assertTrue((out / f"stats-{key}.json").is_file(), "a sealed hour writes its stats file")

    def test_default_bounds_edges(self) -> None:
        # The edges of [10_500, 19_500] seal; one slot outside either edge is refused.
        for span, sealed in ((10_500, True), (19_500, True), (10_499, False), (19_501, False)):
            with self.subTest(span=span):
                key = "2026-10-10T04"
                summary, out = self._run_default_bounds(span, key)
                if sealed:
                    self.assertIsNone(summary.get("stop_reason"))
                    self.assertTrue((out / f"stats-{key}.json").is_file())
                else:
                    self.assertTrue(summary["skipped"])
                    self.assertEqual(summary["stop_reason"], "bad_slot_span")
                    self.assertEqual(summary["slot_span"], span)
                    self.assertFalse((out / f"stats-{key}.json").exists())

    def test_30000_slot_hour_is_still_refused_with_default_bounds(self) -> None:
        # The 20-30k values a slot_for_time bug or resume-inflated counter produced.
        key = "2026-10-10T05"
        summary, out = self._run_default_bounds(30_000, key)
        self.assertTrue(summary["skipped"])
        self.assertEqual(summary["stop_reason"], "bad_slot_span")
        self.assertEqual(summary["slot_span"], 30_000)
        self.assertFalse((out / "trades").exists())
        self.assertFalse((out / f"stats-{key}.json").exists())

    def test_9500_slot_hour_is_still_refused_by_the_walker_minimum(self) -> None:
        key = "2026-10-10T06"
        summary, out = self._run_default_bounds(9_500, key)
        self.assertTrue(summary["skipped"])
        self.assertEqual(summary["stop_reason"], "bad_slot_span")
        self.assertEqual(summary["slot_span"], 9_500)
        self.assertFalse((out / f"stats-{key}.json").exists())

    def test_min_max_slots_per_hour_are_configurable(self) -> None:
        backfill_mod.slots_between = lambda *a, **k: list(range(0, 50))

        def fake_fetch_block(url, slot, limiter, budget=None, header_out=None, *, full=True):
            return {"slot": slot, "blockTime": 0, "transactions": []}, 1, None

        orig_fetch = backfill_mod.fetch_block
        backfill_mod.fetch_block = fake_fetch_block
        self.addCleanup(setattr, backfill_mod, "fetch_block", orig_fetch)
        start_ts, end_ts = _hour_bounds("2026-09-11T07")
        with tempfile.TemporaryDirectory() as tmp:
            summary = run_hour(
                start_ts=start_ts,
                end_ts=end_ts,
                slot_start=0,
                slot_end=50,
                min_slots_per_hour=1,
                max_slots_per_hour=100,
                **self._common_kwargs(tmp),
            )
            self.assertNotEqual(summary.get("stop_reason"), "bad_slot_span")


class RunHourCrashResumeTests(_TimeBoundedTestCase):
    """Exactly-once resume: crash mid-hour (with held rows pending) must equal a clean run."""

    N_SLOTS = 40
    START_KEY = "2026-09-11T08"

    def setUp(self) -> None:
        super().setUp()
        self._orig_slots_between = backfill_mod.slots_between
        self._orig_fetch_block = backfill_mod.fetch_block
        self._orig_rows_from_block = backfill_mod.rows_from_block
        self._orig_fetch_pool_mints = backfill_mod.fetch_pool_mints
        self.addCleanup(setattr, backfill_mod, "slots_between", self._orig_slots_between)
        self.addCleanup(setattr, backfill_mod, "fetch_block", self._orig_fetch_block)
        self.addCleanup(setattr, backfill_mod, "rows_from_block", self._orig_rows_from_block)
        self.addCleanup(setattr, backfill_mod, "fetch_pool_mints", self._orig_fetch_pool_mints)
        backfill_mod.slots_between = lambda *a, **k: list(range(self.N_SLOTS))
        backfill_mod.fetch_pool_mints = lambda url, pools: {p: ("BaseMint", WSOL_MINT) for p in pools}

        def fake_fetch_block(url, slot, limiter, budget=None, header_out=None, *, full=True):
            return {"slot": slot, "blockTime": self.start_ts, "transactions": []}, 1, None

        backfill_mod.fetch_block = fake_fetch_block
        self.start_ts, self.end_ts = _hour_bounds(self.START_KEY)

    def _fake_rows_from_block(self, raise_at: int | None):
        calls = {"n": 0}

        def fake(block, pool_mints, feed=backfill_mod.FEED):
            slot = block["slot"]
            calls["n"] += 1
            if raise_at is not None and calls["n"] == raise_at:
                raise RuntimeError("simulated hard failure mid-hour")
            empty = {"trades": [], "creates": [], "migrations": [], "unresolved": []}
            if slot % 2 == 0:
                empty["trades"] = [
                    {
                        "venue": "pump_bonding",
                        "signature": f"sig{slot}",
                        "event_index": 0,
                        "sol_lamports": 1,
                        "token_raw": 1,
                        "slot": slot,
                    }
                ]
            else:
                empty["unresolved"] = [
                    {
                        "venue": "pumpswap",
                        "signature": f"sig{slot}",
                        "event_index": 0,
                        "pool": "poolX",
                        "sol_lamports": 1,
                        "token_raw": 1,
                        "slot": slot,
                    }
                ]
            return empty

        return fake

    def _kwargs(self, tmp: Path) -> dict:
        limiter = RateLimiter(1000)
        return dict(
            url="http://x",
            start_ts=self.start_ts,
            end_ts=self.end_ts,
            out_dir=tmp,
            limiter=limiter,
            lookup_limiter=limiter,
            pool_mints={},
            workers=1,
            max_bytes=10**9,
            anchor_slot=450278777,
            anchor_time=1790319576,
            slot_start=0,
            slot_end=self.N_SLOTS,
            min_slots_per_hour=1,
            max_slots_per_hour=1000,
        )

    def _signatures(self, out_dir: Path) -> list[str]:
        path = out_dir / "trades" / f"trades-{self.START_KEY}.jsonl.zst"
        self.assertTrue(path.is_file(), f"expected sealed trades file at {path}")
        return [row["signature"] for row in iter_jsonl(path)]

    def test_clean_run_has_every_slot_once(self) -> None:
        backfill_mod.rows_from_block = self._fake_rows_from_block(raise_at=None)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            budget = CreditBudget(10**9, 0)
            summary = run_hour(budget=budget, checkpoint=None, checkpoint_path=None, **self._kwargs(out_dir))
            self.assertEqual(summary["stop_reason"], None)
            sigs = self._signatures(out_dir)
            self.assertEqual(sorted(sigs), sorted(f"sig{i}" for i in range(self.N_SLOTS)))
            self.assertEqual(len(sigs), len(set(sigs)))

    def _crash_then_resume(self, raise_at: int) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            checkpoint_path = out_dir / "checkpoint.json"
            checkpoint = empty_checkpoint()
            budget = CreditBudget(10**9, 0)
            backfill_mod.rows_from_block = self._fake_rows_from_block(raise_at=raise_at)
            with self.assertRaises(RuntimeError):
                run_hour(
                    budget=budget,
                    checkpoint=checkpoint,
                    checkpoint_path=checkpoint_path,
                    **self._kwargs(out_dir),
                )
            entry = checkpoint["hours"][self.START_KEY]
            self.assertEqual(entry["status"], "partial")
            # The bug under test: next_slot must not regress behind what the
            # persisted file offsets already contain, or a resume replays
            # (duplicates) everything already written.
            self.assertIsNotNone(entry["next_slot"])
            self.assertGreater(entry["next_slot"], 0)

            # Resume: no more induced failures.
            backfill_mod.rows_from_block = self._fake_rows_from_block(raise_at=None)
            budget2 = CreditBudget(10**9, 0, used=budget.used)
            summary = run_hour(
                budget=budget2,
                checkpoint=checkpoint,
                checkpoint_path=checkpoint_path,
                **self._kwargs(out_dir),
            )
            self.assertIsNone(summary.get("stop_reason"))
            self.assertEqual(summary["slots"], self.N_SLOTS)
            return self._signatures(out_dir)

    def test_crash_mid_hour_no_duplicates_no_losses(self) -> None:
        for raise_at in (5, 12, 26, 39):
            with self.subTest(raise_at=raise_at):
                sigs = self._crash_then_resume(raise_at)
                self.assertEqual(
                    sorted(sigs),
                    sorted(f"sig{i}" for i in range(self.N_SLOTS)),
                    f"crash at call {raise_at} lost or duplicated rows",
                )
                self.assertEqual(len(sigs), len(set(sigs)), f"crash at call {raise_at} duplicated rows")

    def test_crash_with_held_rows_pending_is_recovered(self) -> None:
        # Odd slots go through the held (unresolved pool lookup) path. Crash
        # right after several have accumulated but before the 250-item or
        # hour-end flush would have written them out.
        sigs = self._crash_then_resume(raise_at=15)
        held_origin_sigs = {f"sig{i}" for i in range(15) if i % 2 == 1}
        self.assertTrue(held_origin_sigs.issubset(set(sigs)))


class SealedButPartialCheckpointHealsTests(_TimeBoundedTestCase):
    """A crash between sealing files and writing checkpoint status="sealed"."""

    def test_resume_heals_instead_of_reprocessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            key = "2026-09-11T09"
            trades_dir = out_dir / "trades"
            trades_dir.mkdir(parents=True)
            sealed_path = trades_dir / f"trades-{key}.jsonl.zst"
            sealed_path.write_bytes(b"not-really-zstd-but-present")
            start_ts, end_ts = _hour_bounds(key)
            checkpoint = empty_checkpoint()
            checkpoint["hours"][key] = {
                "status": "partial",
                "start_slot": 100,
                "end_slot": 12100,
                "next_slot": 5000,
                "stop_reason": None,
                "counts": {"slots_done": 4900},
                "offsets": {"trades": 123, "creates": 0, "migrations": 0},
            }
            checkpoint_path = out_dir / "checkpoint.json"
            limiter = RateLimiter(1000)
            with patch.object(backfill_mod, "slots_between", lambda *a, **k: list(range(100, 12100))):
                summary = run_hour(
                    url="http://x",
                    start_ts=start_ts,
                    end_ts=end_ts,
                    out_dir=out_dir,
                    limiter=limiter,
                    lookup_limiter=limiter,
                    pool_mints={},
                    workers=1,
                    max_bytes=10**9,
                    anchor_slot=450278777,
                    anchor_time=1790319576,
                    budget=CreditBudget(10**9, 0),
                    checkpoint=checkpoint,
                    checkpoint_path=checkpoint_path,
                    slot_start=100,
                    slot_end=12100,
                )
            self.assertEqual(summary.get("stop_reason"), "healed_sealed")
            self.assertEqual(checkpoint["hours"][key]["status"], "sealed")
            # The pre-existing sealed bytes were never touched or duplicated.
            self.assertEqual(sealed_path.read_bytes(), b"not-really-zstd-but-present")

    def test_resume_after_crash_between_seals_seals_the_rest(self) -> None:
        """Crash after trades sealed, while creates/migrations are still plain (and a
        truncated creates .zst exists): resume re-seals from the complete plain files,
        keeps the sealed trades file, and heals without re-fetching."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            key = "2026-09-11T09"
            for sub in ("trades", "creates", "migrations"):
                (out_dir / sub).mkdir(parents=True)
            sealed_trades = out_dir / "trades" / f"trades-{key}.jsonl.zst"
            sealed_trades.write_bytes(b"sealed-trades-bytes")
            plain_creates = out_dir / "creates" / f"creates-{key}.jsonl"
            plain_creates.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
            (out_dir / "creates" / f"creates-{key}.jsonl.zst").write_bytes(b"truncated")
            plain_migr = out_dir / "migrations" / f"migrations-{key}.jsonl"
            plain_migr.write_text("", encoding="utf-8")
            start_ts, end_ts = _hour_bounds(key)
            checkpoint = empty_checkpoint()
            checkpoint["hours"][key] = {
                "status": "partial", "start_slot": 100, "end_slot": 12100, "next_slot": None,
                "stop_reason": None, "counts": {"slots_done": 12000},
                "offsets": {"trades": 0, "creates": 0, "migrations": 0},
            }
            limiter = RateLimiter(1000)
            fetched = []
            with patch.object(backfill_mod, "slots_between", lambda *a, **k: list(range(100, 12100))), \
                 patch.object(backfill_mod, "fetch_block", lambda *a, **k: fetched.append(a) or None, create=True):
                summary = run_hour(
                    url="http://x", start_ts=start_ts, end_ts=end_ts, out_dir=out_dir,
                    limiter=limiter, lookup_limiter=limiter, pool_mints={}, workers=1,
                    max_bytes=10**9, anchor_slot=450278777, anchor_time=1790319576,
                    budget=CreditBudget(10**9, 0), checkpoint=checkpoint,
                    checkpoint_path=out_dir / "checkpoint.json", slot_start=100, slot_end=12100,
                )
            self.assertEqual(summary.get("stop_reason"), "healed_sealed")
            self.assertEqual(checkpoint["hours"][key]["status"], "sealed")
            self.assertEqual(sealed_trades.read_bytes(), b"sealed-trades-bytes")
            self.assertFalse(plain_creates.exists())
            resealed = out_dir / "creates" / f"creates-{key}.jsonl.zst"
            self.assertTrue(resealed.is_file())
            self.assertNotEqual(resealed.read_bytes(), b"truncated")
            self.assertFalse(plain_migr.exists())
            self.assertEqual(fetched, [])


if __name__ == "__main__":
    unittest.main()
