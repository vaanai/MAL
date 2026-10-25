"""Tests for the arena-tape create source and backfill of tools/meta_capture.py. Synthetic tape fixtures only: no real tape,
no network, no RPC."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from tools import meta_arena_source as mas
from tools import meta_capture as mc
from tools.pump_history_backfill import _CREATE_DISC
from tools.test_meta_capture import HK0, HOUR0, MINT, Clock, create_payload, doc_routes, make, run

URI = "ipfs://cid1"


def tape_row(mint: str, t: int, type_: str = "create", prog: str = "pumpfun", sig: str | None = None) -> str:
    return json.dumps({"v": 1, "type": type_, "prog": prog, "slot": 5, "t_recv_ms": t, "signature": sig or "sig-" + mint,
                       "event_index": 0, "mint": mint, "creator": "Cr-" + mint, "name": "n", "symbol": "s"}) + "\n"


def write_tape(d: Path, hk: str, rows: list[str]) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"tape-{hk}.jsonl"
    p.write_text("".join(rows))
    return p


def ok_resolver(sig: str) -> str | None:
    return URI


def creates_rows(col, hk=HK0):
    return [json.loads(x) for x in col.store.creates_path(hk).read_text().splitlines()]


def meta_rows_at(out: Path, hk=HK0):
    return [json.loads(x) for x in (out / "meta" / f"{hk}.jsonl").read_text().splitlines()]


class DefaultUnchangedTests(unittest.TestCase):
    def test_default_rows_have_no_src_and_no_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d)
            col.on_message(create_payload(), HOUR0 + 100)
            col.on_message(create_payload(), HOUR0 + 200)  # a repeat is written again, as before the change
            rows = creates_rows(col)
            self.assertEqual(len(rows), 2)
            for r in rows:
                self.assertEqual(sorted(r), sorted(list(mc.CREATE_ALLOW) + ["t_recv_ms"]))
            self.assertFalse(col.multi_source)
            run(col.step())
            self.assertTrue(all("src" not in m for m in [json.loads(x) for x in col.store.meta_path(HK0).read_text().splitlines()]))

    def test_cli_defaults_leave_second_source_off(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with unittest.mock.patch.dict("sys.modules", {"websockets": unittest.mock.MagicMock()}), \
                    unittest.mock.patch.object(mc, "_main_locked", return_value=0) as m:
                self.assertEqual(mc.main(["--out", d]), 0)
            self.assertIsNone(m.call_args[0][0].arena_tape_dir)
            self.assertFalse(m.call_args[0][0].backfill_from_arena)


class DedupTests(unittest.TestCase):
    def test_one_row_per_mint_across_sources_and_src_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock = make(d)
            col.multi_source = True
            col.on_message(create_payload(), HOUR0 + 100)                       # websocket first
            col.on_message(create_payload(), HOUR0 + 150, src="arena")          # same mint from arena: dropped
            col.on_message(create_payload(mint="MintB"), HOUR0 + 160, src="arena")
            col.on_message(create_payload(mint="MintB"), HOUR0 + 170)           # arena first, websocket second: dropped
            rows = creates_rows(col)
            self.assertEqual([(r["mint"], r["src"]) for r in rows], [(MINT, "pumpportal"), ("MintB", "arena")])
            self.assertEqual(col.src_counts["dup_arena"], 1)
            self.assertEqual(col.src_counts["dup_pumpportal"], 1)
            clock.t = HOUR0 + 1000
            run(col.step())
            self.assertEqual({m["src"] for m in [json.loads(x) for x in col.store.meta_path(HK0).read_text().splitlines()]},
                             {"pumpportal", "arena"})


class TailerTests(unittest.TestCase):
    def test_tail_skips_existing_then_reads_new_whole_lines_of_creates_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tape = Path(d)
            p = write_tape(tape, HK0, [tape_row("Old", 1)])
            t = mas.Tailer(tape)
            self.assertEqual(t.poll(), [])
            with p.open("a") as fh:
                fh.write(tape_row("New1", 2) + tape_row("Tr", 3, type_="trade") + tape_row("Sw", 4, prog="pumpswap")[:-1])
            got = t.poll()
            self.assertEqual([g[0]["mint"] for g in got], ["New1"])        # trade, pumpswap and the torn line are not returned
            with p.open("a") as fh:
                fh.write("\n")
            self.assertEqual(t.poll(), [])                                  # completed line is a pumpswap create: ignored
            self.assertEqual(got[0][0]["traderPublicKey"], "Cr-New1")
            self.assertNotIn("uri", got[0][0])

    def test_uri_from_create_event_logs(self) -> None:
        def s(x: str) -> bytes:
            return len(x.encode()).to_bytes(4, "little") + x.encode()
        raw = _CREATE_DISC + s("name") + s("SYM") + s("https://ipfs.io/ipfs/abc") + b"\0" * 200
        logs = ["Program log: x", "Program data: " + base64.b64encode(raw).decode()]
        self.assertEqual(mas.uri_from_logs(logs), "https://ipfs.io/ipfs/abc")
        self.assertIsNone(mas.uri_from_logs(["Program log: none"]))


class FailoverTests(unittest.TestCase):
    def test_resolver_failure_is_counted_and_retried_websocket_unaffected(self) -> None:
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as td:
            col, _ = make(d)
            col.multi_source = True
            tape = Path(td)
            p = write_tape(tape, HK0, [])
            tailer = mas.Tailer(tape)
            tailer.poll()
            calls = []

            def flaky(sig: str) -> str | None:
                calls.append(sig)
                if len(calls) < 2:
                    raise OSError("boom https://secret.example/key")
                return URI
            with p.open("a") as fh:
                fh.write(tape_row("A1", HOUR0 + 10))
            sem = asyncio.Semaphore(2)
            retry: dict = {}
            now = {"t": 100.0}
            run(mc.arena_round(col, tailer, flaky, sem, retry, now=lambda: now["t"]))
            self.assertEqual(col.store.error_counts, {"resolve_OSError": 1})   # type only, never the message
            self.assertIn("A1", retry)
            col.on_message(create_payload(), HOUR0 + 20)                        # the websocket source still works meanwhile
            run(mc.arena_round(col, tailer, flaky, sem, retry, now=lambda: now["t"]))
            self.assertEqual(len(calls), 1)                                     # backoff: no immediate retry
            now["t"] += mc.ARENA_RETRY_DELAYS_S[0] + 0.1
            run(mc.arena_round(col, tailer, flaky, sem, retry, now=lambda: now["t"]))
            self.assertNotIn("A1", retry)
            self.assertEqual({r["mint"]: r["src"] for r in creates_rows(col)}, {MINT: "pumpportal", "A1": "arena"})

    def test_arena_loop_survives_a_poll_that_raises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d)
            n = {"i": 0}

            class Bad:
                def poll(self):
                    n["i"] += 1
                    raise RuntimeError("x")
            run(mc.arena_loop(col, Bad(), ok_resolver, lambda: n["i"] >= 2, poll_s=0.01))
            self.assertEqual(col.store.error_counts.get("arena_loop_RuntimeError"), 2)

    def test_arena_source_alone_feeds_when_websocket_is_down(self) -> None:
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as td:
            col, _ = make(d)
            col.multi_source = True
            tape = Path(td)
            p = write_tape(tape, HK0, [])
            tailer = mas.Tailer(tape)
            tailer.poll()
            with p.open("a") as fh:
                fh.write(tape_row("A1", HOUR0 + 10) + tape_row("A2", HOUR0 + 11))
            run(mc.arena_round(col, tailer, ok_resolver, asyncio.Semaphore(2), {}))
            self.assertEqual([r["mint"] for r in creates_rows(col)], ["A1", "A2"])
            self.assertEqual({r["uri"] for r in creates_rows(col)}, {URI})


class BackfillTests(unittest.TestCase):
    def _fixture(self, root: Path):
        out, tape = root / "out", root / "tape"
        (out / "creates").mkdir(parents=True)
        (out / "meta").mkdir(parents=True)
        (out / "creates" / f"{HK0}.jsonl").write_text(json.dumps({"mint": "A", "t_recv_ms": HOUR0 + 1}) + "\n")
        (out / "meta" / f"{HK0}.jsonl").write_text(json.dumps({"mint": "B", "attempt": 0, "status": "ok"}) + "\n")
        write_tape(tape, HK0, [tape_row("A", HOUR0 + 1), tape_row("B", HOUR0 + 2), tape_row("C", HOUR0 + 3),
                               tape_row("C", HOUR0 + 4), tape_row("T", HOUR0 + 5, type_="trade")])
        return out, tape

    def test_backfill_skips_existing_rows_and_tags_src(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out, tape = self._fixture(Path(d))
            store = mc.BackfillStore(out / mc.BACKFILL_SUBDIR, "g")
            col, clock = make(d, routes=doc_routes, clock=Clock(HOUR0 + 2 * 3_600_000))
            col.store = store
            counts = run(mc.backfill_run(col, tape, [HK0], out, ok_resolver, HOUR0 + 2 * 3_600_000))
            self.assertEqual(counts, {"rows": 4, "skipped_existing": 3, "fed": 1, "unresolved": 0})
            bdir = out / mc.BACKFILL_SUBDIR
            self.assertEqual([r["mint"] for r in meta_rows_at(bdir)], ["C"])
            self.assertEqual(meta_rows_at(bdir)[0]["src"], "arena-backfill")
            self.assertEqual(meta_rows_at(bdir)[0]["status"], "ok")
            # the live out dir was not written
            self.assertEqual(len((out / "meta" / f"{HK0}.jsonl").read_text().splitlines()), 1)
            self.assertEqual(store.sealed, set())

    def test_rerun_dedups_against_its_own_rows(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out, tape = self._fixture(Path(d))
            for expect_fed in (1, 0):
                col, _ = make(d, routes=doc_routes, clock=Clock(HOUR0 + 2 * 3_600_000))
                col.store = mc.BackfillStore(out / mc.BACKFILL_SUBDIR, "g")
                counts = run(mc.backfill_run(col, tape, [HK0], out, ok_resolver, HOUR0 + 2 * 3_600_000))
                self.assertEqual(counts["fed"], expect_fed)

    def test_open_hour_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out, tape = self._fixture(Path(d))
            col, _ = make(d)
            with self.assertRaises(ValueError):
                run(mc.backfill_run(col, tape, [HK0], out, ok_resolver, HOUR0 + 100))


class BackfillLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._p = None

    def _allow(self, tape: Path):
        return unittest.mock.patch.object(mas, "ALLOWED_TAPE_DIRS", (os.path.realpath(tape),))

    def _argv(self, out: Path, tape: Path, frm: str, to: str) -> list[str]:
        return ["--out", str(out), "--arena-tape-dir", str(tape), "--backfill-from-arena", "--from", frm, "--to", to,
                "--tx-url-env", "TEST_TX_URL"]

    def test_refuses_when_another_backfill_holds_its_lock(self) -> None:
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"TEST_TX_URL": "https://example.invalid"}):
            out, tape = Path(d) / "out", Path(d) / "tape"
            tape.mkdir()
            fd = mc.acquire_single_instance(out / mc.BACKFILL_SUBDIR)
            try:
                with self._allow(tape):
                    self.assertEqual(mc.main(self._argv(out, tape, "2026-10-25T00", "2026-10-25T01")), mc.EXIT_LOCKED)
            finally:
                os.close(fd)

    def test_live_lock_does_not_block_and_backfill_never_touches_it(self) -> None:
        # Design: the backfill has its own lock and out subdir, so a live process keeps running and the two cannot interleave
        # writes into the sealed chain. An empty hour range runs the whole path with no fetches.
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"TEST_TX_URL": "https://example.invalid"}):
            out, tape = Path(d) / "out", Path(d) / "tape"
            tape.mkdir()
            live = mc.acquire_single_instance(out)
            try:
                with self._allow(tape):
                    self.assertEqual(mc.main(self._argv(out, tape, "2026-10-25T00", "2026-10-25T00")), 0)
                self.assertTrue((out / mc.BACKFILL_SUBDIR / ".lock").exists())
                self.assertFalse((out / "creates").exists())
            finally:
                os.close(live)

    def test_requires_tx_url_env_and_range(self) -> None:
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {}, clear=False):
            out, tape = Path(d) / "out", Path(d) / "tape"
            tape.mkdir()
            os.environ.pop("TEST_TX_URL", None)
            with self._allow(tape):
                self.assertEqual(mc.main(self._argv(out, tape, "2026-10-25T00", "2026-10-25T00")), 2)
            with unittest.mock.patch.dict("sys.modules", {"websockets": unittest.mock.MagicMock()}):
                self.assertEqual(mc.main(["--out", str(out), "--arena-tape-dir", str(tape), "--tx-url-env", "X"]), 2)


class GuardTests(unittest.TestCase):
    def test_forbidden_tokens_and_non_allowlisted_dirs_refused(self) -> None:
        for bad in ("/data/mal/arena/haiku-v4", "/data/mal/arena/ledger/tape", "/data/mal/arena/tape_shakedown",
                    "/data/mal/arena/smoke", "/data/mal/arena/shadow/tape", "/x/fwdv1", "/x/holdout/tape", "/data/mal/arena/tape-copy",
                    "/data/mal/arena", "/tmp"):
            with self.assertRaises(ValueError, msg=bad):
                mas.check_tape_dir(bad)
        self.assertEqual(mas.check_tape_dir("/data/mal/arena/tape"), Path("/data/mal/arena/tape"))

    def test_resolved_symlink_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "ledger"
            real.mkdir()
            link = Path(d) / "plain"
            link.symlink_to(real)
            with unittest.mock.patch.object(mas, "ALLOWED_TAPE_DIRS", (str(link),)):
                with self.assertRaises(ValueError):
                    mas.check_tape_dir(link)

    def test_cli_refuses_before_any_read(self) -> None:
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"TEST_TX_URL": "https://example.invalid"}):
            tape = Path(d) / "tape"
            tape.mkdir()
            out = Path(d) / "out"
            argv = ["--out", str(out), "--arena-tape-dir", str(tape), "--tx-url-env", "TEST_TX_URL"]
            self.assertEqual(mc.main(argv), 2)           # not allowlisted
            self.assertFalse(out.exists())               # nothing created, no lock


class BackfillRangeTests(unittest.TestCase):
    def _run(self, frm: str, to: str) -> int:
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"TEST_TX_URL": "https://example.invalid"}):
            tape = Path(d) / "tape"
            tape.mkdir()
            out = Path(d) / "out"
            with unittest.mock.patch.object(mas, "ALLOWED_TAPE_DIRS", (os.path.realpath(tape),)):
                rc = mc.main(["--out", str(out), "--arena-tape-dir", str(tape), "--backfill-from-arena", "--from", frm,
                              "--to", to, "--tx-url-env", "TEST_TX_URL"])
            self.assertFalse((out / mc.BACKFILL_SUBDIR / ".lock").exists() and rc == 2, "no lock before a refused range")
            return rc

    def test_from_before_floor_refused(self) -> None:
        self.assertEqual(mc.BACKFILL_FLOOR, "2026-10-25T00")
        self.assertEqual(self._run("2026-10-24T23", "2026-10-25T01"), 2)

    def test_open_or_just_closed_hour_refused_cleanly(self) -> None:
        self.assertEqual(self._run("2026-10-25T00", "2099-01-01T00"), 2)

    def test_floor_hour_accepted(self) -> None:
        self.assertEqual(self._run("2026-10-25T00", "2026-10-25T00"), 0)


class BackfillRowsTests(unittest.TestCase):
    def test_failed_rows_carry_src_and_backfill_and_schedule_is_from_feed_time(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, clock = make(d, clock=Clock(HOUR0 + 2 * 3_600_000))        # every gateway fails
            col.store = mc.BackfillStore(Path(d) / "bf", "g")
            col.on_message(create_payload(), HOUR0 + 100, src="arena-backfill") if False else None
            col.multi_source = True
            col.on_message(create_payload(), HOUR0 + 100, src="arena-backfill")
            row = json.loads(col.store.creates_path(HK0).read_text().splitlines()[0])
            self.assertTrue(row["backfill"])
            self.assertEqual(row["t_fed_ms"], HOUR0 + 2 * 3_600_000)
            self.assertEqual(col.heap[0][0], HOUR0 + 2 * 3_600_000)         # due from feed time, not the old receive time
            run(col.step())
            self.assertEqual(col.heap[0][0], HOUR0 + 2 * 3_600_000 + 30_000)  # next attempt +30 s, like live
            m = [json.loads(x) for x in col.store.meta_path(HK0).read_text().splitlines()]
            self.assertTrue(all(r["src"] == "arena-backfill" and r["backfill"] is True for r in m))
            self.assertNotEqual(m[0]["status"], "ok")

    def test_default_failed_rows_have_no_src(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d)
            col.on_message(create_payload(), HOUR0 + 100)
            run(col.step())
            for x in col.store.meta_path(HK0).read_text().splitlines():
                self.assertNotIn("src", json.loads(x))


class ResolverTests(unittest.TestCase):
    def _fake(self, body: dict):
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, n): return json.dumps(body).encode()
        return unittest.mock.patch("urllib.request.urlopen", lambda *a, **k: R())

    def test_jsonrpc_error_in_200_is_transient_not_no_uri(self) -> None:
        r = mas.make_rpc_resolver("https://example.invalid")
        with self._fake({"error": {"code": 429, "message": "rate limited"}}):
            with self.assertRaises(mas.TxLookupError):
                r("sig")
        with self._fake({"result": None}):
            self.assertIsNone(r("sig"))

    def test_error_body_backs_off_in_live_mode(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            col, _ = make(d)
            col.multi_source = True
            calls = []

            def res(sig):
                calls.append(sig)
                raise mas.TxLookupError("rpc_error")
            retry = {"M": ({"mint": "M", "signature": "s"}, HOUR0, 0, 0.0)}

            class T:
                def poll(self): return []
            run(mc.arena_round(col, T(), res, asyncio.Semaphore(1), retry, now=lambda: 10.0))
            run(mc.arena_round(col, T(), res, asyncio.Semaphore(1), retry, now=lambda: 10.5))
            self.assertEqual(len(calls), 1)
            self.assertEqual(col.store.error_counts, {"resolve_TxLookupError": 1})
            self.assertGreater(retry["M"][3], 10.5)

    def test_live_retry_window_is_about_a_minute(self) -> None:
        self.assertGreaterEqual(sum(mc.ARENA_RETRY_DELAYS_S), 60)

    def test_rate_limiter_spaces_calls(self) -> None:
        slept = []
        clock = {"t": 0.0}

        async def fake_sleep(x):
            slept.append(x)
            clock["t"] += x
        lim = mas.RateLimiter(2.0, clock=lambda: clock["t"])
        with unittest.mock.patch.object(mas.asyncio, "sleep", fake_sleep):
            async def go():
                for _ in range(3):
                    await lim.acquire()
            run(go())
        self.assertEqual(slept, [0.5, 0.5])

    def test_resolver_uses_its_own_pool(self) -> None:
        self.assertIsNot(mas.resolver_pool(), None)
        self.assertTrue(mas.resolver_pool()._thread_name_prefix.startswith("tx-resolve"))


class HourFilesTests(unittest.TestCase):
    def test_zst_preferred_when_both_exist(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tape = Path(d)
            (tape / f"tape-{HK0}.jsonl").write_text("")
            (tape / f"tape-{HK0}.jsonl.zst").write_bytes(b"")
            self.assertEqual([p.name for p in mas.hour_files(tape, [HK0])], [f"tape-{HK0}.jsonl.zst"])


if __name__ == "__main__":
    unittest.main()
