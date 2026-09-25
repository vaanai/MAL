"""Hourly seal, zstd, retention, and completeness. No network."""

from __future__ import annotations

import json
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from observe.trade_decode import WSOL_MINT
from observe.trade_source import SOURCE_PUBLIC_RPC_LOGS
from observe.trade_store import (
    HEADROOM_RATIO,
    RESUME_RATIO,
    CompletenessMonitor,
    DiskGuard,
    HourlyJsonlWriter,
    ZstdCompressor,
    compress_sealed,
    keep_days_for,
    stored_trade,
)
from observe.trade_tape import PoolMintCache, accept_trade, seed_pool_cache
from observe.test_trade_decode import SIG, SLOT, T_RECV_MS, _line
from observe.trade_decode import records_from_logs


class _Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when

    def __call__(self) -> datetime:
        return self.now


class _Comp:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def enqueue(self, path: Path) -> None:
        self.paths.append(path)


class _Stats:
    def __init__(self) -> None:
        self.reconnects = 0


def _rich_swap() -> dict:
    return records_from_logs(
        [_line("pumpswap_sell_event.b64")],
        slot=SLOT,
        signature=SIG,
        t_recv_ms=T_RECV_MS,
        commitment="confirmed",
        feed=SOURCE_PUBLIC_RPC_LOGS,
    )[0]


class StoreTests(unittest.TestCase):
    def test_pumpswap_stored_row_keeps_path_pnl_and_edges(self) -> None:
        rich = _rich_swap()
        self.assertEqual(rich["type"], "trade")
        self.assertIn("t_recv", rich)
        self.assertIn("market_cap_sol", rich)
        slim = stored_trade(rich)
        self.assertEqual(slim["v"], 2)
        for key in (
            "venue",
            "mint",
            "trader",
            "side",
            "sol_lamports",
            "token_raw",
            "quote_reserve",
            "base_reserve",
            "price_sol",
            "pool",
            "slot",
            "signature",
            "event_index",
            "t_recv_ms",
            "event_ts",
        ):
            self.assertEqual(slim[key], rich[key], key)
        for dropped in (
            "type",
            "feed",
            "mint_source",
            "sol",
            "token",
            "market_cap_sol",
            "market_cap_supply_ui",
            "t_recv",
            "commitment",
        ):
            self.assertNotIn(dropped, slim)
        wsol = stored_trade({**rich, "quote_mint": WSOL_MINT, "quote_is_wsol": True})
        self.assertEqual(wsol["quote_mint"], WSOL_MINT)
        self.assertTrue(wsol["quote_is_wsol"])
        other = stored_trade({**rich, "quote_mint": "NotWsolMint111111111111111111111111111", "quote_is_wsol": False})
        self.assertEqual(other["quote_mint"], "NotWsolMint111111111111111111111111111")
        self.assertFalse(other["quote_is_wsol"])
        flagged = stored_trade({**rich, "zero_sol": True, "quote_mint": WSOL_MINT, "quote_is_wsol": True})
        self.assertTrue(flagged["zero_sol"])
        self.assertEqual(flagged["quote_mint"], WSOL_MINT)
        self.assertTrue(flagged["quote_is_wsol"])
        bonding = stored_trade({**rich, "venue": "pump_bonding"})
        self.assertEqual(bonding["v"], 1)
        self.assertEqual(bonding["type"], "trade")
        self.assertNotIn("quote_is_wsol", slim)

    def test_seeded_pool_cache_stamps_quote_on_the_next_trade(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pool-mints-2026-09-25T08.jsonl").write_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "pool_mint",
                        "pool": "HwK2JkkHc5Ekt6umApmj5RerhugNSiMULioThvKvkGB9",
                        "mint": "Mint111111111111111111111111111111111111111",
                        "quote_mint": WSOL_MINT,
                        "quote_is_wsol": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            cache = PoolMintCache()
            self.assertEqual(seed_pool_cache(root, cache), 1)
            from observe.trade_source import RawNotice

            note = RawNotice(
                slot=SLOT,
                signature=SIG,
                failed=False,
                logs=(_line("pumpswap_buy_event.b64"),),
                t_recv_ms=T_RECV_MS,
                commitment="confirmed",
                feed=SOURCE_PUBLIC_RPC_LOGS,
            )
            from observe.trade_tape import trades_from_notice

            ready = cache.accept(trades_from_notice(note, cache.mints), now=1.0)
            self.assertEqual(len(ready), 1)
            self.assertTrue(ready[0]["quote_is_wsol"])
            self.assertEqual(ready[0]["quote_mint"], WSOL_MINT)
            stored = stored_trade(ready[0])
            self.assertTrue(stored["quote_is_wsol"])
            self.assertEqual(stored["quote_mint"], WSOL_MINT)
            bare = {**_rich_swap()}
            guard = DiskGuard(root, fs_bytes=lambda _p: (1000, 10, 900))
            stats = _Stats()
            monitor = CompletenessMonitor(root, root / "missing", stats, start_at_end=False)
            writer = HourlyJsonlWriter(
                root, "trades", _Comp(), clock=_Clock(datetime(2026, 9, 25, 8, tzinfo=timezone.utc))
            )
            self.assertFalse(accept_trade(guard, monitor, writer, bare))
            self.assertEqual(writer.rows, 0)
            self.assertTrue(accept_trade(guard, monitor, writer, ready[0]))
            line = json.loads(writer.path.read_text(encoding="utf-8"))
            self.assertTrue(line["quote_is_wsol"])
            self.assertEqual(line["quote_mint"], WSOL_MINT)
            writer.close()

    def test_hourly_roll_seals_the_previous_hour(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clock = _Clock(datetime(2026, 9, 25, 7, 10, tzinfo=timezone.utc))
            comp = _Comp()
            sealed: list[tuple[str, float]] = []
            writer = HourlyJsonlWriter(
                root, "trades", comp, clock=clock, on_seal=lambda stamp, span: sealed.append((stamp, span))
            )
            writer.write({"n": 1})
            self.assertEqual(writer.path.name, "trades-2026-09-25T07.jsonl")
            clock.now = datetime(2026, 9, 25, 8, 0, 5, tzinfo=timezone.utc)
            writer.rotate()
            self.assertEqual(comp.paths[0].name, "trades-2026-09-25T07.jsonl")
            self.assertIn('"n":1', comp.paths[0].read_text(encoding="utf-8"))
            self.assertEqual(sealed[0][0], "2026-09-25T07")
            self.assertGreaterEqual(sealed[0][1], 3000)
            writer.write({"n": 2})
            self.assertEqual((root / "trades-2026-09-25T08.jsonl").read_text(encoding="utf-8").strip(), '{"n":2}')
            writer.close()

    def test_keep_days_leaves_twenty_percent_headroom(self) -> None:
        self.assertAlmostEqual(HEADROOM_RATIO, 0.20)
        self.assertGreater(RESUME_RATIO, HEADROOM_RATIO)
        # 100 bytes total, 10 used outside the tape, 5 bytes/day -> 14 days.
        self.assertEqual(keep_days_for(100, 10, 0, 5), 14)
        self.assertEqual(keep_days_for(100, 90, 10, 5), 0)

    def test_retention_deletes_old_tape_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "trades-2026-01-01T00.jsonl.zst"
            recent = root / "pool-mints-2026-09-24T03.jsonl.zst"
            stats = root / "stats-2026-01-01.jsonl"
            other = root / "observe-2026-01-01.jsonl"
            note = root / "readme.txt"
            old.write_bytes(b"x" * 100)
            recent.write_bytes(b"y" * 100)
            stats.write_text("{}\n", encoding="utf-8")
            other.write_text("{}\n", encoding="utf-8")
            note.write_text("keep", encoding="utf-8")
            link = root / "trades-2020-01-01T00.jsonl"
            link.symlink_to(other)
            guard = DiskGuard(root, fs_bytes=lambda _p: (100_000, 200, 99_800))
            guard.tick(
                now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
                open_paths=set(),
                inflight=set(),
                current_hour="2026-09-25T12",
            )
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(stats.exists())
            self.assertTrue(other.exists())
            self.assertTrue(note.exists())
            self.assertTrue(link.is_symlink())
            self.assertGreaterEqual(guard.keep_days or 0, 1)

    def test_headroom_hold_does_not_touch_open_or_foreign_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "trades-2026-09-20T01.jsonl.zst"
            live = root / "trades-2026-09-25T12.jsonl"
            stats = root / "stats-2026-09-25.jsonl"
            note = root / "pg-data.txt"
            old.write_bytes(b"z" * 40)
            live.write_text('{"live":1}\n', encoding="utf-8")
            stats.write_text("{}\n", encoding="utf-8")
            note.write_text("postgres\n", encoding="utf-8")
            avail = {"n": 100}

            def fs(_p: Path) -> tuple[int, int, int]:
                return (1000, 900, avail["n"])

            guard = DiskGuard(root, fs_bytes=fs)
            guard.tick(
                now=datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc),
                open_paths={live.resolve()},
                inflight=set(),
                current_hour="2026-09-25T12",
            )
            self.assertFalse(old.exists())
            self.assertTrue(live.exists())
            self.assertTrue(stats.exists())
            self.assertTrue(note.exists())
            self.assertTrue(guard.holding)
            avail["n"] = 220
            guard.tick(now=datetime(2026, 9, 25, 12, 31, tzinfo=timezone.utc), open_paths={live.resolve()})
            self.assertTrue(guard.holding)
            avail["n"] = 250
            guard.tick(now=datetime(2026, 9, 25, 12, 32, tzinfo=timezone.utc), open_paths={live.resolve()})
            self.assertFalse(guard.holding)

    def test_hold_skips_the_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = DiskGuard(root, fs_bytes=lambda _p: (100, 90, 5))
            guard.holding = True
            stats = _Stats()
            monitor = CompletenessMonitor(root, root / "missing", stats, start_at_end=False)
            writer = HourlyJsonlWriter(root, "trades", _Comp(), clock=_Clock(datetime(2026, 9, 25, 1, tzinfo=timezone.utc)))
            rich = {**_rich_swap(), "quote_mint": WSOL_MINT, "quote_is_wsol": True}
            self.assertFalse(accept_trade(guard, monitor, writer, rich))
            self.assertEqual(guard.dropped, 1)
            self.assertEqual(writer.rows, 0)
            guard.holding = False
            self.assertTrue(accept_trade(guard, monitor, writer, rich))
            self.assertEqual(writer.rows, 1)
            line = json.loads(writer.path.read_text(encoding="utf-8"))
            self.assertEqual(line["v"], 2)
            self.assertEqual(line["signature"], SIG)
            writer.close()

    def test_stats_score_the_previous_create_window(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            observe = root / "observe"
            observe.mkdir()
            tape = root / "tape"
            tape.mkdir()
            (observe / "observe-2026-09-25.jsonl").write_text(
                json.dumps(
                    {"stream": "subscribeNewToken", "txType": "create", "mint": "MintA", "t_ws": "2026-09-25T08:00:00.000+00:00"}
                )
                + "\n",
                encoding="utf-8",
            )
            stats = _Stats()
            clock = _Clock(datetime(2026, 9, 25, 8, 10, tzinfo=timezone.utc))
            monitor = CompletenessMonitor(
                tape, observe, stats, interval_s=600, clock=clock, start_at_end=False
            )
            monitor.start(0.0)
            monitor.note(
                {
                    "venue": "pump_bonding",
                    "mint": "MintA",
                    "t_recv_ms": 1_790_318_932_500,
                    "event_ts": 1790318931,
                }
            )
            first = monitor.flush(600.0, {"free_bytes": 10, "total_bytes": 100, "hold": False})
            self.assertEqual(first["creates"], 0)
            self.assertIsNone(first["pct_creates_with_bonding_trade"])
            self.assertEqual(first["bonding"], 1)
            self.assertEqual(first["trades"], 1)
            self.assertAlmostEqual(first["lag_p50_s"], 1.5)
            clock.now = datetime(2026, 9, 25, 8, 20, tzinfo=timezone.utc)
            stats.reconnects = 2
            second = monitor.flush(1200.0, {"free_bytes": 10, "total_bytes": 100, "hold": False})
            self.assertEqual(second["creates"], 1)
            self.assertEqual(second["creates_with_bonding_trade"], 1)
            self.assertEqual(second["pct_creates_with_bonding_trade"], 100.0)
            self.assertEqual(second["reconnects"], 2)
            self.assertEqual(second["trades"], 0)
            written = (tape / "stats-2026-09-25.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(written), 2)
            self.assertEqual(json.loads(written[1])["type"], "tape_stats")

    @unittest.skipUnless(shutil.which("zstd"), "zstd missing")
    def test_zstd_seals_legacy_daily_and_reports_a_ratio(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "trades-2026-09-24.jsonl"
            current = root / "trades-2026-09-25T08.jsonl"
            line = json.dumps(_rich_swap(), separators=(",", ":")) + "\n"
            legacy.write_text(line * 30, encoding="utf-8")
            current.write_text(line, encoding="utf-8")
            raw = legacy.stat().st_size
            comp = ZstdCompressor()
            try:
                comp.sweep_startup(root, "2026-09-25T08")
                comp.close(timeout_s=30)
            finally:
                if comp._thread.is_alive():
                    comp.close(timeout_s=5)
            dest = Path(str(legacy) + ".zst")
            self.assertFalse(legacy.exists())
            self.assertTrue(dest.exists())
            self.assertTrue(current.exists())
            self.assertFalse(Path(str(current) + ".zst").exists())
            self.assertGreater(comp.raw_bytes, 0)
            ratio = comp.raw_bytes / comp.zst_bytes
            self.assertGreater(ratio, 2.0)
            self.assertEqual(comp.raw_bytes, raw)
            roundtrip = root / "back.jsonl"
            subprocess_ok = shutil.which("zstd")
            self.assertIsNotNone(subprocess_ok)
            import subprocess

            subprocess.run(
                [subprocess_ok, "-d", "-q", "-f", "-o", str(roundtrip), str(dest)],
                check=True,
            )
            self.assertEqual(roundtrip.read_text(encoding="utf-8"), line * 30)

    @unittest.skipUnless(shutil.which("zstd"), "zstd missing")
    def test_compress_sealed_replaces_raw(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pool-mints-2026-09-25T01.jsonl"
            path.write_text('{"pool":"p"}\n' * 20, encoding="utf-8")
            raw, zst = compress_sealed(path)
            self.assertFalse(path.exists())
            self.assertGreater(raw, zst)
            self.assertTrue(Path(str(path) + ".zst").is_file())


if __name__ == "__main__":
    unittest.main()
