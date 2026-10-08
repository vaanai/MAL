"""Fixtures only: no /data/mal read. A tiny LightGBM model is trained here (as test_forward_exp012_gate does)."""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from tools import cap_pick_gate_replay as cp
from tools.forward_paper import replay_rows
from tools.test_forward_exp012_gate import NAMES, _gated, _make_model, _mint_rows
from tools.test_forward_paper import T0, _create, _trade

DAY0 = 1_700_000_000_250 // 86_400_000 * 86_400_000  # 00:00Z of the fixture day
HOUR = 3_600_000


def _dump(row: dict) -> str:
    return json.dumps(row, separators=(",", ":"))


def _crow(mint: str, t_ms: int, creator: str = "CA", sig: str | None = None, t_recv: bool = True) -> dict:
    row = {"type": "create", "mint": mint, "creator": creator, "block_time": t_ms // 1000, "event_ts": t_ms // 1000,
           "signature": sig or "sig-" + mint, "quote_reserve": 35_000_000_000, "base_reserve": 1_073_000_000_000_000,
           "quote_mint": "So11111111111111111111111111111111111111112"}
    if t_recv:
        row["t_recv_ms"] = t_ms
    return row


def _null_recv(row: dict) -> dict:
    """A getBlock backfill row: t_recv_ms null, chain time only."""
    r = dict(row)
    r["t_recv_ms"] = None
    r["block_time"] = r["event_ts"]
    return r


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        model, md5, feats = _make_model(self.dir)
        self.model, self.md5, self.feats = model, md5, feats

    def engine(self):
        return cp.build_engine(self.model, self.md5, self.feats, 0.5, kill_dir=self.dir)

    def rep(self, **kw) -> cp.Replayer:
        r = cp.Replayer(self.engine(), "fix", **kw)
        r.boot(DAY0, None)
        return r


class RefusalTests(unittest.TestCase):
    def test_sealed_pools_hours_and_runner_output(self) -> None:
        root = "/data/mal/clean-view/explore-0814/w1"
        cp.refuse_path(f"{root}/trades/trades-2026-08-26T12.jsonl.zst", [root])
        for bad in (
            "/data/mal/clean-view/fresh-0802/w1/trades/trades-2026-08-04T12.jsonl.zst",
            "/data/mal/blocks-clean/fresh-0808/w1/trades/trades-2026-08-10T12.deduped.jsonl.zst",
            "/data/mal/blocks-clean/fresh-0828/w1/creates/creates-2026-08-30T12.deduped.jsonl.zst",
            "/data/mal/clean-view/oracle-live-2026-09-25_27/trades/trades-2026-09-25T12.jsonl.zst",
            "/var/lib/mal/paper/forward-paper/exp012-gate.jsonl",
            "/x/arm-audit.jsonl",
            "/x/positions-2026-09-30.jsonl",
        ):
            with self.assertRaises(cp.Refused, msg=bad):
                cp.refuse_path(bad)
        # EXP-009 hours [2026-09-15T12, 2026-09-18T23) and anything at or after 2026-10-02T10Z
        for hour in ("2026-09-15T12", "2026-09-17T03", "2026-09-18T22", "2026-10-02T10", "2026-10-05T00"):
            with self.assertRaises(cp.Refused, msg=hour):
                cp.refuse_path(f"{root}/trades/trades-{hour}.jsonl.zst")
        for hour in ("2026-09-15T11", "2026-09-18T23", "2026-10-02T09"):
            cp.refuse_path(f"{root}/trades/trades-{hour}.jsonl.zst")
        with self.assertRaises(cp.Refused):
            cp.refuse_path("/data/mal/clean-view/explore-0814/w1/trades/notes.txt")
        with self.assertRaises(cp.Refused):
            cp.refuse_path("/data/mal/other/trades/trades-2026-08-26T12.jsonl.zst", [root])

    def test_hour_listing_skips_refused_hours_and_replay_refuses_days(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            for h in ("2026-09-15T11", "2026-09-15T12", "2026-09-18T23"):
                for kind in ("creates", "trades"):
                    d = Path(td) / kind
                    d.mkdir(exist_ok=True)
                    (d / f"{kind}-{h}.jsonl.zst").write_bytes(b"")
            files = cp.hour_files(cp.Block("t", (td,), ".jsonl.zst"))
            self.assertEqual(sorted(files), ["2026-09-15T11", "2026-09-18T23"])
            with self.assertRaises(cp.Refused):
                cp.replay_view(cp.Block("t", (td,), ".jsonl.zst"), ["2026-09-16"], roots=[td])
            with self.assertRaises(cp.Refused):
                cp.replay_view(cp.Block("t", (td,), ".jsonl.zst"), ["2026-10-03"], roots=[td])
            cp.check_days(["2026-09-15", "2026-09-18", "2026-10-02"])  # partly allowed days: refused hours are never listed
            with self.assertRaises(cp.Refused):
                cp.check_days(["2026-09-17"])
            with self.assertRaises(cp.Refused):
                list(cp.iter_lines(Path(td) / "trades" / "trades-2026-09-15T12.jsonl.zst", [td]))


class OrderTests(_Base):
    def _events(self, rep: cp.Replayer, mint: str) -> list[tuple]:
        return list(rep.engine.exp012.acc[mint].feat.events)

    def test_runner_order_not_file_order(self) -> None:
        rep = self.rep()
        t = DAY0 + 5 * HOUR
        c = _crow("M", t)
        same_sec = [  # same second: ordered by (slot, tx_index, event_index), not by file order
            dict(_trade("M", t + 2_000, trader="w1", slot=12, event_index=0, quote=40_000_000_000), tx_index=5),
            dict(_trade("M", t + 2_000, trader="w2", slot=11, event_index=1, quote=41_000_000_000), tx_index=9),
            dict(_trade("M", t + 2_000, trader="w3", slot=11, event_index=0, quote=42_000_000_000), tx_index=9),
            dict(_trade("M", t + 2_000, trader="w4", slot=11, event_index=0, quote=43_000_000_000), tx_index=2),
        ]
        rep.feed_hour([c], [_dump(r) for r in same_sec])
        traders = [e[2] for e in self._events(rep, "M")]
        self.assertEqual(traders, ["w4", "w3", "w2", "w1"])

    def test_time_beats_slot_and_creates_come_first(self) -> None:
        rep = self.rep()
        t = DAY0 + 5 * HOUR
        late_low_slot = dict(_trade("M", t + 3_000, trader="late", slot=1), tx_index=0)
        early_high_slot = dict(_trade("M", t + 1_000, trader="early", slot=99), tx_index=0)
        # the print is listed before the create in the stream and shares the create's second
        at_create = dict(_trade("M", t, trader="atcreate", slot=50), tx_index=0)
        rep.feed_hour([_crow("M", t)], [_dump(r) for r in (late_low_slot, at_create, early_high_slot)])
        self.assertEqual([e[2] for e in self._events(rep, "M")], ["atcreate", "early", "late"])

    def test_null_tx_index_uses_file_order_and_is_flagged(self) -> None:
        rep = self.rep(null_tx_index=True)
        t = DAY0 + 5 * HOUR
        rows = [
            dict(_trade("M", t + 2_000, trader="a", slot=7, event_index=0), signature="s2"),
            dict(_trade("M", t + 2_000, trader="b", slot=7, event_index=0), signature="s1"),
            dict(_trade("M", t + 2_000, trader="c", slot=7, event_index=0), signature="s3"),
        ]
        for r in rows:
            r.pop("tx_index", None)
        rep.feed_hour([_crow("M", t)], [_dump(r) for r in rows])
        self.assertEqual([e[2] for e in self._events(rep, "M")], ["a", "b", "c"])
        rep.feed_hour([], [_dump(_trade("M", t + 9_000, venue="pumpswap", slot=20, quote=70_000_000_000, base=cp_B0()))])
        self.assertTrue(rep.records[0]["tx_index_null"])

    def test_dedupe_counts_a_repeated_print_once(self) -> None:
        rep = self.rep()
        t = DAY0 + 5 * HOUR
        r = dict(_trade("M", t + 2_000, trader="a", slot=7), tx_index=1)
        rep.feed_hour([_crow("M", t)], [_dump(r), _dump(r)])
        self.assertEqual(len(self._events(rep, "M")), 1)


def cp_B0() -> int:
    from tools.test_forward_paper import B0

    return B0 // 2


class RestartAndSkipTests(_Base):
    def test_restart_drops_state_and_prior_mint_gets_no_decision(self) -> None:
        creates = self.dir / "creates"
        creates.mkdir()
        t_old = DAY0 - 2 * HOUR
        hour = cp._hour_of_ms(t_old)
        (creates / f"creates-{hour}.jsonl").write_text(_dump(_crow("OLD", t_old, "CZ")) + "\n", encoding="utf-8")
        rep = cp.Replayer(self.engine(), "fix")
        n = rep.boot(DAY0, creates)
        self.assertEqual(n, 1)
        self.assertIn("OLD", rep.dead)
        t = DAY0 + 3 * HOUR
        rows = _mint_rows("NEW", 7, t0=t)
        old_rows = [dict(_trade("OLD", t + 1_000, trader="w"), tx_index=1),
                    dict(_trade("OLD", t + 20_000, venue="pumpswap", slot=100, quote=70_000_000_000, base=cp_B0()), tx_index=2)]
        rep.feed_hour([_crow("NEW", t, "CN")], [_dump(r) for r in rows + old_rows])
        self.assertEqual([r["mint"] for r in rep.records], ["NEW"])
        self.assertEqual([r["mint"] for r in rep.dead_records()], ["OLD"])
        self.assertEqual(rep.dead_records()[0]["decision"], "pre_restart")
        # a restart empties the library, the accumulators and the dedupe state
        rep.boot(DAY0 + 24 * HOUR, creates)
        self.assertEqual((len(rep.lib), len(rep.engine.exp012.acc), rep.prints), (0, 0, 0))
        self.assertIn("NEW", rep.decided)

    def test_daily_restart_in_replay_view(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            for kind in ("creates", "trades"):
                (Path(td) / kind).mkdir()
            sfx = ".jsonl.zst"
            # plain-text content under a .jsonl name would not match the suffix, so use an uncompressed suffix
            blk = cp.Block("t", (td,), ".jsonl", False)
            t = DAY0 - HOUR + 20 * 60_000  # created 23:20 the day before the first replayed day
            hour_c = cp._hour_of_ms(t)
            (Path(td) / "creates" / f"creates-{hour_c}.jsonl").write_text(_dump(_crow("PRE", t, "CP")) + "\n", encoding="utf-8")
            (Path(td) / "trades" / f"trades-{hour_c}.jsonl").write_text(_dump(dict(_trade("PRE", t + 5_000), tx_index=1)) + "\n", encoding="utf-8")
            t1 = DAY0 + 2 * HOUR
            h1 = cp._hour_of_ms(t1)
            rows = _mint_rows("D1", 7, t0=t1) + [dict(_trade("PRE", t1 + 1_000, venue="pumpswap", slot=5, quote=70_000_000_000, base=cp_B0()), tx_index=3)]
            (Path(td) / "creates" / f"creates-{h1}.jsonl").write_text(_dump(_crow("D1", t1, "CD")) + "\n", encoding="utf-8")
            (Path(td) / "trades" / f"trades-{h1}.jsonl").write_text("\n".join(_dump(r) for r in rows) + "\n", encoding="utf-8")
            day = cp._day_of_ms(DAY0)
            recs, meta = cp.replay_view(blk, [day], engine=self.engine(), roots=[td])
            self.assertEqual({r["mint"]: r["kind"] for r in recs}, {"D1": "decision", "PRE": "dead"})
            self.assertEqual(meta["boots"][0]["history_rows"], 1)
            self.assertTrue(sfx)

    def test_zst_history_is_read_through_staging(self) -> None:
        """The real pools are .zst; zstd -dc refuses symlinks, so staging must copy (history was 0 with symlinks)."""
        import shutil
        import subprocess

        if shutil.which("zstd") is None:
            self.skipTest("zstd binary not available")
        with tempfile.TemporaryDirectory() as td:
            for kind in ("creates", "trades"):
                (Path(td) / kind).mkdir()
            t = DAY0 - HOUR + 20 * 60_000
            hc = cp._hour_of_ms(t)
            raw = Path(td) / "creates" / f"creates-{hc}.jsonl"
            raw.write_text(_dump(_crow("PRE", t, "CP")) + "\n", encoding="utf-8")
            subprocess.run(["zstd", "-q", "--rm", str(raw)], check=True)
            (Path(td) / "trades" / f"trades-{hc}.jsonl.zst").write_bytes(b"")
            blk = cp.Block("t", (td,), ".jsonl.zst", False)
            recs, meta = cp.replay_view(blk, [cp._day_of_ms(DAY0)], engine=self.engine(), roots=[td])
            self.assertEqual(meta["boots"][0]["history_rows"], 1)
            self.assertEqual(meta["boots"][0]["staged_files"], 1)

    def test_migration_after_60_minutes_is_no_features(self) -> None:
        rep = self.rep(prune_every=1)
        t = DAY0 + 2 * HOUR
        slow = _mint_rows("SLOW", 7, t0=t, mig_after_ms=61 * 60_000)  # migrates 61 min after create
        fast = _mint_rows("FAST", 7, t0=t, mig_after_ms=59 * 60_000)  # 59 min: still gated
        # a print of another mint at 60 min 30 s drives the prune, as the runner's own print counter does
        tick = dict(_trade("FAST", t + 60 * 60_000 + 30_000, trader="tick", slot=60), tx_index=0)
        rows = sorted(slow + fast + [tick], key=lambda r: r["t_recv_ms"])
        rep.feed_hour([_crow("SLOW", t, "C1"), _crow("FAST", t, "C2")], [_dump(r) for r in rows])
        by = {r["mint"]: r for r in rep.records}
        self.assertEqual(by["SLOW"]["decision"], "no_features")
        self.assertIsNone(by["SLOW"]["score"])
        self.assertGreater(by["SLOW"]["ttm_s"], 3600)
        self.assertIn(by["FAST"]["decision"], ("pick", "below"))
        self.assertLessEqual(by["FAST"]["ttm_s"], 3600)
        self.assertIsNotNone(by["FAST"]["score"])

    def test_no_bond_history_and_label_mapping(self) -> None:
        rep = self.rep()
        t = DAY0 + 2 * HOUR
        rep.feed_hour([_crow("NB", t)], [_dump(dict(_trade("NB", t + 5_000, venue="pumpswap", slot=9, quote=70_000_000_000, base=cp_B0()), tx_index=1))])
        self.assertEqual(rep.records[0]["decision"], "no_bond_history")


class RunnerEquivalenceTests(_Base):
    def test_matches_replay_rows_on_a_fixture(self) -> None:
        """The same prints through the runner's own replay_rows and through the Replayer: same score, decision, mig_ms."""
        mints = {"Hi": 7, "Lo": 1, "Mid": 5}
        creates = [_create(m, T0, "C" + m) for m in mints]
        rows = [r for m, n in mints.items() for r in _mint_rows(m, n)]
        rows.sort(key=lambda r: r["t_recv_ms"])
        eng = replay_rows(creates, rows, [_gated(self.model, self.md5, self.feats)], tape_end_ms=T0 + 120_000,
                          kill_file=self.dir / "KILL", offsets_ms=(5_000,))
        want = {r["mint"]: r for r in eng.exp012_rows}
        rep = self.rep()
        crows = [_crow(c.mint, c.t_signal_ms, c.creator, c.signature) for c in creates]
        rep.feed_hour(crows, [_dump(r) for r in rows])
        got = {r["mint"]: r for r in rep.records}
        self.assertEqual(set(got), set(want))
        for m, w in want.items():
            self.assertEqual(got[m]["mig_ms"], w["mig_ms"])
            self.assertEqual(got[m]["entered"], w["entered"])
            self.assertTrue(math.isclose(got[m]["score"], w["score"], rel_tol=1e-12, abs_tol=1e-12), m)
            self.assertEqual(got[m]["time_fallbacks"], w["time_fallbacks"])
        self.assertTrue(got["Hi"]["entered"])
        self.assertEqual(got["Hi"]["decision"], "pick")
        self.assertEqual(got["Lo"]["decision"], "below")

    def test_null_t_recv_is_imputed_from_block_time(self) -> None:
        rep = self.rep()
        t = DAY0 + 2 * HOUR
        rows = [_null_recv(r) for r in _mint_rows("Q", 7, t0=t)]
        rep.feed_hour([_null_recv(_crow("Q", t))], [_dump(r) for r in rows])
        self.assertEqual(rep.records[0]["decision"], "pick")
        self.assertEqual(rep.imputed[0], 1)
        self.assertGreater(rep.imputed_prints, 0)
        self.assertEqual(rep.records[0]["mig_ms"] % 1000, 0)


class CompareTests(unittest.TestCase):
    def _on(self, mint, decision, score, ttm, day="2026-08-15", view="v"):
        return {"view": view, "mint": mint, "day": day, "decision": decision, "score": score, "ttm_s": ttm, "tx_index_null": False}

    def _off(self, mint, score, ttm, date="2026-08-15", view="v"):
        return {"view": view, "date": date, "score": score, "ttm_s": ttm}

    def test_overlap_delta_bands_and_labels(self) -> None:
        th = cp.THRESHOLD
        on = {
            "a": self._on("a", "pick", 0.90, 600.0),
            "b": self._on("b", "pick", 0.85, 2400.0),
            "c": self._on("c", "below", 0.10, 100.0),
            "d": self._on("d", "no_features", None, 4000.0),
            "x": self._on("x", "pick", 0.99, 50.0, day="2026-08-16"),  # outside the replayed days
        }
        off = {
            "a": self._off("a", 0.91, 600.0),
            "b": self._off("b", 0.30, 2400.0),  # online-only pick
            "c": self._off("c", 0.10, 100.0),
            "d": self._off("d", 0.95, 4000.0),  # offline-only pick: slow migration
            "e": self._off("e", 0.96, 700.0),  # offline-only pick: pre-restart
            "f": self._off("f", 0.20, 800.0),
            "z": self._off("z", 0.99, 700.0, date="2026-08-16"),
        }
        out = cp.compare(on, {"e": {"mint": "e"}}, off, {"v": {"days": ["2026-08-15"]}}, th)["views"]["v"]
        self.assertEqual(out["picks"], {"online": 2, "offline": 3, "both": 1, "online_only": 1, "online_only_absent_from_offline_table": 0,
                                        "offline_only": 2, "jaccard": 0.25})
        self.assertEqual((out["n_online_decisions"], out["n_online_scored"], out["n_offline_scored"]), (4, 3, 6))
        self.assertEqual(out["abs_score_delta"]["n"], 3)
        self.assertAlmostEqual(out["abs_score_delta"]["max"], 0.55)
        lab = out["labels_by_ttm_band"]
        self.assertEqual(lab["online:no_features"], {"gt60m": 1, "all": 1})
        self.assertEqual(lab["online:pick"], {"le32m": 1, "32_60m": 1, "all": 2})
        self.assertEqual(lab["offline_only_no_online:pre_restart"], {"le32m": 1, "all": 1})
        self.assertEqual(lab["offline_only_no_online:no_record"], {"le32m": 1, "all": 1})
        self.assertEqual(out["offline_picks_by_band"]["gt60m"]["offline_pick"], 1)
        self.assertEqual(out["offline_picks_by_band"]["gt60m"]["online_pick_of_those"], 0)

    def test_bands(self) -> None:
        self.assertEqual([cp.band_of(x) for x in (0, 1920, 1920.5, 3600, 3601, None)], ["le32m", "le32m", "32_60m", "32_60m", "gt60m", "unknown"])

    def test_load_offline_uses_oof_for_p1(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            csv_p, oof_p = Path(td) / "cache.csv", Path(td) / "oof.json"
            csv_p.write_text("date,mint,source,score,f0\n2026-08-15,m1,P2,0.9,10\n2026-09-19,m2,P1A,0.1,20\n2026-09-26,m3,P1B,0.9,30\n2026-08-15,m4,P2,,5\n", encoding="utf-8")
            oof_p.write_text(json.dumps({"rows": [{"mint": "m2", "score": 0.85}, {"mint": "m3", "score": 0.99}]}), encoding="utf-8")
            off = cp.load_offline(csv_p, oof_p)
            self.assertEqual(sorted(off), ["m1", "m2"])
            self.assertEqual((off["m2"]["view"], off["m2"]["score"], off["m2"]["ttm_s"]), ("fast-pool-0918", 0.85, 20.0))
            self.assertEqual(off["m1"]["view"], "explore-0814")

    def test_restatement_join(self) -> None:
        def r(day, mint, status, pnl, blk="explore-0814"):
            return {"day": day, "blk": blk, "mint": mint, "status": status, "pnl": pnl, "fee": 55_000, "ssb": 1, "nearby": 1e9, "size": 500_000_000}

        rows = [r("2026-08-15", "a", "fill", 50_000_000.0), r("2026-08-15", "b", "guard_fail", -55_000.0), r("2026-08-16", "c", "fill", -25_000_000.0),
                r("2026-08-16", "d", "fill", 10_000_000.0), r("2026-08-17", "e", "fill", 99_000_000.0)]
        on = {m: {"view": "explore-0814", "day": d, "decision": "pick", "mint": m} for m, d in (("a", "2026-08-15"), ("c", "2026-08-16"), ("e", "2026-08-17"))}
        off = {"a": {"view": "explore-0814", "date": "2026-08-15", "score": 0.9, "ttm_s": 100.0},
               "d": {"view": "explore-0814", "date": "2026-08-16", "score": 0.95, "ttm_s": 5000.0},
               "c": {"view": "explore-0814", "date": "2026-08-16", "score": 0.1, "ttm_s": 5.0}}
        meta = {"explore-0814": {"days": ["2026-08-15", "2026-08-16"]}}  # 08-17 is not replayed: "e" is out
        rep = cp.restatement_report(on, off, rows, meta)
        g = rep["explore-0814"]
        self.assertEqual(g["online_pick"]["live"]["n"], 2)  # a, c
        self.assertEqual(g["offline_all_picks"]["live"]["n"], 2)  # a, d
        self.assertEqual(g["offline_picks_le60min"]["live"]["n"], 1)  # a only (d is 5000 s)
        live = 100 * ((1 - 1 / 62) * 50_000_000 + (1 / 62) * -55_000) / 500_000_000
        self.assertAlmostEqual(g["offline_picks_le60min"]["live"]["mean_pct"], live, places=9)
        self.assertEqual(g["offline_picks_le60min"]["live"]["days_pos"], "1/1")
        self.assertEqual(rep["P2-P4"]["online_pick"]["live"]["n"], 2)
        self.assertNotIn("P1", rep)
        self.assertEqual(rep["coverage"]["online_pick"], {"picks": 2, "with_a_G_attempt_row": 2})
        self.assertEqual(rep["coverage"]["offline_all_picks"], {"picks": 2, "with_a_G_attempt_row": 2})

    def test_leg_stats_date_cluster(self) -> None:
        x = [100e6, 100e6, -50e6, 100e6]  # lamports
        s = cp.leg_stats(x, ["d1", "d1", "d2", "d3"], 500e6)
        self.assertEqual(s["days_pos"], "2/3")
        self.assertAlmostEqual(s["total_sol"], 0.25)
        self.assertAlmostEqual(s["ex_best_day_sol"], 0.05)  # best day is d1 (0.2 SOL)
        self.assertAlmostEqual(s["mean_pct"], 100 * 0.0625 / 0.5)
        self.assertEqual(s, cp.leg_stats(x, ["d1", "d1", "d2", "d3"], 500e6))  # seed 1: repeatable


# ---- strict lines (audit A8): a NUL, a truncated final line or a non-JSON line is a refusal, never a skip ----------
BAD_PIECES = {  # name -> (bytes of the third line, kind tape_lines gives it)
    "nul": (b'{"type":"trade","mint":"M\x00M"}\n', "nul"),
    "truncated_final_line": (b'{"type":"trade","mint":"M","side":"bu', "not_json"),
    "garbage": (b"this is not json\n", "not_json"),
    "array": (b"[1,2,3]\n", "non_object"),
}


def _write_hour(root: Path, kind: str, hour: str, chunks: list[bytes], *, zst: bool) -> Path:
    d = root / kind
    d.mkdir(exist_ok=True)
    raw = d / f"{kind}-{hour}.jsonl"
    raw.write_bytes(b"".join(chunks))
    if zst:
        subprocess.run(["zstd", "-q", "--rm", str(raw)], check=True)
        return d / f"{kind}-{hour}.jsonl.zst"
    return raw


def _lines(rows: list[dict]) -> list[bytes]:
    return [(_dump(r) + "\n").encode() for r in rows]


class StrictLinesTests(_Base):
    def _case(self, zst: bool):
        if zst and shutil.which("zstd") is None:
            self.skipTest("zstd binary not available")
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return Path(td.name), cp.Block("t", (td.name,), ".jsonl.zst" if zst else ".jsonl", False)

    def _assert_refused(self, ctx, path: Path, line: int, kind: str) -> None:
        msg = str(ctx.exception)
        self.assertIn(str(path), msg)
        self.assertIn(f"physical line {line}", msg)
        self.assertIn(f"kind {kind}", msg)
        self.assertIsInstance(ctx.exception, cp.BadLineRefused)
        self.assertEqual((ctx.exception.line, ctx.exception.kind), (line, kind))

    def test_bad_trades_line_refuses_plain_and_zst(self) -> None:
        t = DAY0 + 2 * HOUR
        hour = cp._hour_of_ms(t)
        for zst in (False, True):
            for name, (bad, kind) in BAD_PIECES.items():
                with self.subTest(zst=zst, case=name):
                    root, blk = self._case(zst)
                    _write_hour(root, "creates", hour, _lines([_crow("M", t)]), zst=zst)
                    good = _lines([dict(_trade("M", t + 1_000, trader="a"), tx_index=1), dict(_trade("M", t + 2_000, trader="b"), tx_index=2)])
                    path = _write_hour(root, "trades", hour, good + [bad], zst=zst)
                    with self.assertRaises(cp.Refused) as ctx:
                        cp.replay_view(blk, [cp._day_of_ms(DAY0)], engine=self.engine(), roots=[str(root)])
                    self._assert_refused(ctx, path, 3, kind)

    def test_bad_creates_line_refuses_plain_and_zst(self) -> None:
        t = DAY0 + 2 * HOUR
        hour = cp._hour_of_ms(t)
        for zst in (False, True):
            for name, (bad, kind) in BAD_PIECES.items():
                with self.subTest(zst=zst, case=name):
                    root, blk = self._case(zst)
                    good = _lines([_crow("M", t), _crow("N", t + 500, "CN")])
                    path = _write_hour(root, "creates", hour, good + [bad], zst=zst)
                    _write_hour(root, "trades", hour, _lines([dict(_trade("M", t + 1_000), tx_index=1)]), zst=zst)
                    with self.assertRaises(cp.Refused) as ctx:
                        cp.replay_view(blk, [cp._day_of_ms(DAY0)], engine=self.engine(), roots=[str(root)])
                    self._assert_refused(ctx, path, 3, kind)

    def test_bad_line_in_boot_staging_refuses_plain_and_zst(self) -> None:
        """preload swallows every error, so staging must scan each creates file it copies."""
        t = DAY0 - 2 * HOUR  # history hour: staged for the 00:00Z boot of DAY0, never fed
        hour = cp._hour_of_ms(t)
        for zst in (False, True):
            for name, (bad, kind) in BAD_PIECES.items():
                with self.subTest(zst=zst, case=name):
                    root, blk = self._case(zst)
                    good = _lines([_crow("OLD", t, "CO"), _crow("OLD2", t + 500, "CO2")])
                    path = _write_hour(root, "creates", hour, good + [bad], zst=zst)
                    files = cp.hour_files(blk, [str(root)])
                    with tempfile.TemporaryDirectory() as dest, self.assertRaises(cp.Refused) as ctx:
                        cp.stage_creates(files, DAY0, Path(dest), [str(root)])
                    self._assert_refused(ctx, path, 3, kind)
                    # and through replay_view, where the refusal must not be swallowed by preload
                    with self.assertRaises(cp.Refused):
                        cp.replay_view(blk, [cp._day_of_ms(DAY0)], engine=self.engine(), roots=[str(root)])

    def test_truncated_zst_stream_refuses(self) -> None:
        root, blk = self._case(True)
        t = DAY0 + 2 * HOUR
        hour = cp._hour_of_ms(t)
        _write_hour(root, "creates", hour, _lines([_crow("M", t)]), zst=True)
        rows = [dict(_trade("M", t + 1_000 + i, trader=f"w{i}", slot=i), tx_index=i) for i in range(4000)]
        path = _write_hour(root, "trades", hour, _lines(rows), zst=True)
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
        with self.assertRaises(cp.Refused) as ctx:
            cp.replay_view(blk, [cp._day_of_ms(DAY0)], engine=self.engine(), roots=[str(root)])
        self.assertIn(str(path), str(ctx.exception))

    def test_clean_files_are_untouched(self) -> None:
        """Blank lines are not bad; a clean file yields exactly the lines it holds."""
        for zst in (False, True):
            with self.subTest(zst=zst):
                root, _blk = self._case(zst)
                t = DAY0 + 2 * HOUR
                hour = cp._hour_of_ms(t)
                body = _lines([_crow("M", t)]) + [b"\n"] + _lines([_crow("N", t + 1_000, "CN")])
                path = _write_hour(root, "creates", hour, body, zst=zst)
                self.assertEqual([r["mint"] for r in cp.iter_json_rows(path, [str(root)])], ["M", "N"])
                self.assertEqual(len(list(cp.iter_lines(path, [str(root)]))), 3)

    def test_main_exits_3_on_refused(self) -> None:
        root, blk = self._case(False)
        t = DAY0 + 2 * HOUR
        hour = cp._hour_of_ms(t)
        _write_hour(root, "creates", hour, _lines([_crow("M", t)]), zst=False)
        _write_hour(root, "trades", hour, _lines([dict(_trade("M", t + 1_000), tx_index=1)]) + [b"not json\n"], zst=False)
        out = root / "out.jsonl"
        argv = ["replay", "--view", "fix", "--from-day", cp._day_of_ms(DAY0), "--out", str(out)]
        err = io.StringIO()
        engine = self.engine()
        with mock.patch.dict(cp.BLOCKS, {"fix": blk}), mock.patch.object(cp, "build_engine", lambda *a, **k: engine), redirect_stderr(err):
            self.assertEqual(cp.main(argv), 3)
        self.assertIn("REFUSED", err.getvalue())
        self.assertIn("physical line 2", err.getvalue())
        self.assertFalse(out.exists())  # nothing is written after a refusal
        # every other Refused is 3 too (a sealed day), and the exploration refusals are unchanged
        err = io.StringIO()
        with mock.patch.dict(cp.BLOCKS, {"fix": blk}), redirect_stderr(err):
            self.assertEqual(cp.main(["replay", "--view", "fix", "--from-day", "2026-10-03", "--out", str(out)]), 3)


class WiringTests(unittest.TestCase):
    def test_frozen_inputs(self) -> None:
        import hashlib

        self.assertEqual(hashlib.md5(cp.MODEL.read_bytes()).hexdigest(), cp.frozen_model_md5())
        self.assertEqual(json.loads((cp.REPO / "ARTIFACTS" / "exp012" / "threshold.json").read_text())["threshold"], cp.THRESHOLD)
        self.assertEqual(list(json.loads(cp.FEATURES.read_text())["frozen_feature_names"]), NAMES)

    def test_drop_constant_matches_the_gate(self) -> None:
        from tools.forward_exp012_gate import DROP_AFTER_CREATE_MS

        self.assertEqual(cp.DROP_AFTER_CREATE_MS, DROP_AFTER_CREATE_MS)

    def test_quick_mint(self) -> None:
        self.assertEqual(cp.quick_mint('{"venue":"x","mint":"AbC","quote_mint":"So1","mint_source":"event"}'), "AbC")
        self.assertEqual(cp.quick_mint('{"quote_mint":"So1","mint_source":"e"}'), None)
        self.assertEqual(cp.quick_mint('{"mint": "Spaced"}'), "Spaced")


if __name__ == "__main__":
    unittest.main()
