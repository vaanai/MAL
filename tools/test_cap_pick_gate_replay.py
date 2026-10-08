"""Fixtures only: no /data/mal read. A tiny LightGBM model is trained here (as test_forward_exp012_gate does)."""

from __future__ import annotations

import hashlib
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


# ---- the no-grant path is unchanged ---------------------------------------------------------------------------------
def golden_fixture(root: Path, *, zst: bool = False) -> list[str]:
    """Two replayed UTC days of a small clean view (creates + trades hour files under `root`). Returns the days.
    It covers a pick, a below, a 61-minute no_features, a mint created before the 00:00Z restart, creator history
    from before day 0, and the second daily boot."""
    creates: dict[str, list[dict]] = {}
    trades: dict[str, list[dict]] = {}

    def add_create(row: dict) -> None:
        creates.setdefault(cp._hour_of_ms(row["t_recv_ms"]), []).append(row)

    def add_trades(rows: list[dict]) -> None:
        for r in rows:
            trades.setdefault(cp._hour_of_ms(r["t_recv_ms"]), []).append(r)

    t_old = DAY0 - 5 * HOUR
    add_create(_crow("H1", t_old + 1_000, "C1"))
    add_create(_crow("H2", t_old + 2_000, "C1"))
    t_pre = DAY0 - HOUR + 20 * 60_000  # created 23:20 the day before day 0: no decision, a pre_restart record
    add_create(_crow("PRE", t_pre, "CP"))
    add_trades([dict(_trade("PRE", t_pre + 5_000), tx_index=1)])
    t2 = DAY0 + 2 * HOUR
    for mint, creator, t0 in (("Hi", "C1", t2), ("Lo", "C2", t2 + 30_000), ("SLOW", "C3", t2 + 60_000)):
        add_create(_crow(mint, t0, creator))
    add_trades(_mint_rows("Hi", 7, t0=t2) + _mint_rows("Lo", 1, t0=t2 + 30_000) + _mint_rows("SLOW", 7, t0=t2 + 60_000, mig_after_ms=61 * 60_000))
    add_trades([dict(_trade("PRE", t2 + 90_000, venue="pumpswap", slot=5, quote=70_000_000_000, base=cp_B0()), tx_index=3)])
    # a print 60 min 30 s after SLOW's create drives the prune (replay with prune_every=1), as in the 60-minute test
    add_trades([dict(_trade("SLOW", t2 + 60_000 + 60 * 60_000 + 30_000, trader="tick", slot=60), tx_index=0)])
    t_late = DAY0 + 23 * HOUR + 50 * 60_000  # created before the second boot, migrates after it
    add_create(_crow("LATE", t_late, "C4"))
    add_trades(_mint_rows("LATE", 6, t0=t_late, mig_after_ms=20 * 60_000))
    t5 = DAY0 + 24 * HOUR + 5 * HOUR  # day 1
    add_create(_crow("D1Hi", t5, "C1"))
    add_create(_crow("D1Lo", t5 + 10_000, "C5"))
    add_trades(_mint_rows("D1Hi", 7, t0=t5) + _mint_rows("D1Lo", 2, t0=t5 + 10_000))
    for hour, rows in sorted(creates.items()):
        _write_hour(root, "creates", hour, _lines(rows), zst=zst)
    for hour, rows in sorted(trades.items()):
        rows.sort(key=lambda r: r["t_recv_ms"])
        _write_hour(root, "trades", hour, _lines(rows), zst=zst)
    return [cp._day_of_ms(DAY0), cp._day_of_ms(DAY0 + 24 * HOUR)]


def canonical(recs: list[dict], meta: dict) -> str:
    return json.dumps({"records": recs, "meta": meta}, sort_keys=True)


# sha256 of canonical(records, meta) for golden_fixture(), computed by the module at 8fd49e5 (before any read-ready change)
# with the model below and prune_every=1. The same digest from the working tree is the "no grant: byte-identical" proof.
GOLDEN_SHA256 = "c721097c0e1febd0866d46fa6a36bd94db4bbd839e3e15ab103182681a7535e2"
GOLDEN_MODEL_MD5 = "05892b317dce5afdeda0465ad32c46dd"


class NoGrantUnchangedTests(_Base):
    def _run(self, zst: bool) -> tuple[list[dict], dict]:
        if zst and shutil.which("zstd") is None:
            self.skipTest("zstd binary not available")
        with tempfile.TemporaryDirectory() as td:
            days = golden_fixture(Path(td), zst=zst)
            blk = cp.Block("t", (td,), ".jsonl.zst" if zst else ".jsonl", False)
            return cp.replay_view(blk, days, engine=self.engine(), roots=[td], prune_every=1)

    def test_output_is_byte_identical_to_the_pre_change_module(self) -> None:
        if self.md5 != GOLDEN_MODEL_MD5:
            self.skipTest(f"fixture model md5 {self.md5} differs from the golden's (lightgbm build); compare by hand")
        recs, meta = self._run(False)
        self.assertEqual({(r["kind"], r["decision"]) for r in recs},
                         {("dead", "pre_restart"), ("decision", "below"), ("decision", "no_features"), ("decision", "pick")})
        self.assertEqual(hashlib.sha256(canonical(recs, meta).encode()).hexdigest(), GOLDEN_SHA256)

    def test_zst_and_plain_give_the_same_output(self) -> None:
        self.assertEqual(canonical(*self._run(False)), canonical(*self._run(True)))

    def test_public_surface_is_frozen(self) -> None:
        """The E0 harness is written against these: the schema, the record keys and replay_view's positional parameters."""
        import inspect

        params = list(inspect.signature(cp.replay_view).parameters.values())
        self.assertEqual([p.name for p in params if p.kind is not p.KEYWORD_ONLY], ["block", "days"])
        self.assertEqual([p.name for p in params if p.kind is p.KEYWORD_ONLY],
                         ["daily_restart", "prune_every", "create_time", "roots", "engine", "log", "grant", "_test_final_ledger"])
        recs, _meta = self._run(False)
        by_kind = {r["kind"]: sorted(r) for r in recs}
        self.assertEqual(by_kind["decision"], sorted(["schema", "kind", "view", "mint", "day", "mig_ms", "create_ms", "ttm_s", "score", "decision",
                                                      "entered", "error", "time_fallbacks", "tx_index_null"]))
        self.assertEqual(by_kind["dead"], sorted(["schema", "kind", "view", "mint", "first_pumpswap_ms", "day", "decision", "tx_index_null"]))
        self.assertEqual({r["schema"] for r in recs}, {cp.SCHEMA})

    def test_no_grant_keys_in_meta_and_cli_never_builds_a_grant(self) -> None:
        recs, meta = self._run(False)
        self.assertNotIn("grant", meta)
        self.assertEqual(list(meta), ["view", "days", "daily_restart", "prune_every", "create_time", "boots", "create_rows_t_recv_imputed",
                                      "trade_rows_t_recv_imputed", "tx_index_null_view", "guesses"])
        self.assertEqual(cp.SCHEMA, "cap_pick_gate_replay_v1")
        seen: dict = {}

        def fake(block, days, **kw):
            seen.update(kw)
            return [], {"view": block.name, "days": list(days)}

        with tempfile.TemporaryDirectory() as td, mock.patch.object(cp, "replay_view", fake), redirect_stderr(io.StringIO()):
            cp.main(["replay", "--view", "explore-0814", "--from-day", "2026-08-15", "--out", str(Path(td) / "o.jsonl")])
        self.assertNotIn("grant", seen)
        self.assertNotIn("grant", [a.dest for a in cp.build_parser()._subparsers._group_actions[0].choices["replay"]._actions])


# ---- sealed walk-2 read mode: ReadGrant --------------------------------------------------------------------------------
B16 = cp._calendar_ms("2026-10-16")  # 00:00Z of the first walk-2 day (the first boot)


def walk2_fixture(walk: Path, fwd: Path, *, event_v: bool = False) -> list[str]:
    """Walker-layout hour files for 2026-10-16 and 2026-10-17: forward-1002 buffer files under `fwd` (creates before
    10-16T00, creates and trades of 10-16T00), walk-2 files under `walk`. Sealed with the walker's own seal_jsonl.
    event_v adds the walk-2 trade keys to every trade row."""
    from tools.pump_history_backfill import seal_jsonl

    creates: dict[str, list[dict]] = {}
    trades: dict[str, list[dict]] = {}

    def add_create(row: dict) -> None:
        creates.setdefault(cp._hour_of_ms(row["t_recv_ms"]), []).append(row)

    def add_trades(rows: list[dict]) -> None:
        for r in rows:
            trades.setdefault(cp._hour_of_ms(r["t_recv_ms"]), []).append(r)

    add_create(_crow("H1", B16 - 4 * HOUR + 1_000, "C1"))
    add_create(_crow("H2", B16 - 4 * HOUR + 2_000, "C1"))
    t_pre = B16 - HOUR + 20 * 60_000  # created 23:20 on 10-15: a pre_restart record when it migrates
    add_create(_crow("PRE", t_pre, "CP"))
    t_e0 = B16 + 10 * 60_000  # created in the 10-16T00 buffer hour, migrates in it
    add_create(_crow("E0m", t_e0, "C1"))
    add_trades(_mint_rows("E0m", 7, t0=t_e0))
    t2 = B16 + 2 * HOUR
    for mint, creator, t0 in (("Hi", "C1", t2), ("Lo", "C2", t2 + 30_000)):
        add_create(_crow(mint, t0, creator))
    add_trades(_mint_rows("Hi", 7, t0=t2) + _mint_rows("Lo", 1, t0=t2 + 30_000))
    add_trades([dict(_trade("PRE", t2 + 90_000, venue="pumpswap", slot=5, quote=70_000_000_000, base=cp_B0()), tx_index=3)])
    t_late = B16 + 23 * HOUR + 50 * 60_000
    add_create(_crow("LATE", t_late, "C4"))
    add_trades(_mint_rows("LATE", 6, t0=t_late, mig_after_ms=20 * 60_000))
    t5 = B16 + 24 * HOUR + 5 * HOUR
    add_create(_crow("D1Hi", t5, "C1"))
    add_create(_crow("D1Lo", t5 + 10_000, "C5"))
    add_trades(_mint_rows("D1Hi", 7, t0=t5) + _mint_rows("D1Lo", 2, t0=t5 + 10_000))
    if event_v:
        for rows in trades.values():
            for r in rows:
                r.update(EVENT_V_ROW_KEYS)
    for kind, by_hour in (("creates", creates), ("trades", trades)):
        for hour, rows in sorted(by_hour.items()):
            root = fwd if (hour < "2026-10-16T01") else walk
            (root / kind).mkdir(parents=True, exist_ok=True)
            rows.sort(key=lambda r: r["t_recv_ms"])
            plain = root / kind / f"{kind}-{hour}.jsonl"
            plain.write_bytes(b"".join(_lines(rows)))
            assert seal_jsonl(plain) == root / kind / f"{kind}-{hour}.jsonl.zst"
    return ["2026-10-16", "2026-10-17"]


# the keys --event-v adds to a walk-2 trade row (observe.trade_decode.EVENT_V_KEYS), with plausible values
EVENT_V_ROW_KEYS = {"virtual_quote_reserves": 17_580_000_000, "ix_name": "buy", "creator_fee_unclaimed": 123_456, "buyback_fee": 777,
                    "fee_recipient_zero": False}


class GrantTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        if shutil.which("zstd") is None:
            self.skipTest("zstd binary not available")
        self.walk = self.dir / "walk2"
        self.fwd = self.dir / "fwd1002"
        self.ledger = self.dir / "ledger" / "FINAL_READS.jsonl"
        for name, val in (("WALK2_DIR", str(self.walk)), ("FWD1002_DIR", str(self.fwd))):
            p = mock.patch.object(cp, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.days = walk2_fixture(self.walk, self.fwd)
        self.write_final()

    def write_final(self, **kw) -> None:
        from tools import exp012_forward as ef

        out_dir = self.dir / "final_out"
        out_dir.mkdir(exist_ok=True)
        (out_dir / ef.LOCK_NAME).write_bytes(b"lock")
        ef.ledger_append(self.ledger, ef._final_docs(out_dir, "a" * 64, kw.get("clean", "2026-10-06T00:00:00Z"),
                                                    kw.get("end", "2026-10-16T00:00:00Z"), kw.get("test_window", False),
                                                    kw.get("experiment", "EXP-012")))

    def grant(self, drop=(), extra=None) -> cp.ReadGrant:
        hours: dict[str, dict] = {}
        for root in (self.walk, self.fwd):
            for kind in cp.GRANT_KINDS:
                d = root / kind
                if not d.is_dir():
                    continue
                for f in sorted(d.iterdir()):
                    hour = f.name[len(kind) + 1: -len(".jsonl.zst")]
                    if (hour, kind) not in drop:
                        hours.setdefault(hour, {})[kind] = (str(f), hashlib.sha256(f.read_bytes()).hexdigest())
        for hour, kinds in (extra or {}).items():
            hours.setdefault(hour, {}).update(kinds)
        return cp.ReadGrant(hours)

    def run_grant(self, grant=None, days=None, **kw):
        return cp.replay_view(cp.WALK2_BLOCK, days or self.days, engine=self.engine(), grant=grant or self.grant(),
                              _test_final_ledger=kw.pop("ledger", self.ledger), **kw)

    # --- it reads what a plain replay reads ---------------------------------------------------------------
    def test_grant_mode_decides_exactly_as_the_exploration_replay_on_the_same_files(self) -> None:
        recs, meta = self.run_grant()
        self.assertEqual({r["mint"]: r["decision"] for r in recs if r["kind"] == "decision"},
                         {"E0m": "pick", "Hi": "pick", "Lo": "below", "D1Hi": "pick", "D1Lo": "below"})
        self.assertEqual({r["mint"] for r in recs if r["kind"] == "dead"}, {"PRE", "LATE"})
        # the same files through the no-grant path (its cutoff lifted for the test) give the same records and meta
        blk = cp.Block(cp.WALK2_BLOCK.name, (str(self.walk), str(self.fwd)), ".jsonl.zst", False)
        with mock.patch.object(cp, "CUTOFF", "2099-01-01T00"):
            recs2, meta2 = cp.replay_view(blk, self.days, engine=self.engine(), roots=[str(self.walk), str(self.fwd)])
        self.assertEqual(recs, recs2)
        g = meta.pop("grant")
        for b in meta["boots"]:
            self.assertIsInstance(b.pop("staging_hours_not_in_grant"), list)
        self.assertEqual(canonical(recs, meta), canonical(recs2, meta2))
        self.assertTrue(g["forward_1002_granted"])
        self.assertEqual(g["final_ledger"], str(self.ledger))
        self.assertTrue(g["final_ledger_overridden"])  # a test run says so in its meta
        self.assertEqual(g["replayed_hours_not_in_grant"][:2], ["2026-10-16T01", "2026-10-16T03"])
        opened = {(h, k) for h, k, _sha in g["opened"]}
        self.assertIn(("2026-10-16T00", "trades"), opened)
        self.assertIn(("2026-10-15T23", "creates"), opened)  # boot staging
        self.assertEqual({(h, k): sha for h, k, sha in g["opened"]}[("2026-10-16T02", "trades")],
                         hashlib.sha256((self.walk / "trades" / "trades-2026-10-16T02.jsonl.zst").read_bytes()).hexdigest())

    def test_first_boot_reads_the_documented_forward_1002_hours(self) -> None:
        hours = cp._staging_hours(B16)
        self.assertEqual((len(hours), hours[0], hours[-1]), (27, "2026-10-14T21", "2026-10-15T23"))
        self.assertTrue(all(cp.FWD1002_BUFFER[0] <= h < cp.FWD1002_BUFFER[1] for h in hours))
        self.assertEqual(cp.FWD1002_CREATES_HOURS[0], hours[0])  # the creates bound is the first boot's staging window
        self.assertEqual(cp.FWD1002_CREATES_HOURS[1], cp.FWD1002_BUFFER[1])  # plus feed hour 2026-10-16T00
        self.assertTrue(cp.FWD1002_BUFFER[0] <= cp.FWD1002_CREATES_HOURS[0] and cp.FWD1002_CREATES_HOURS[1] <= cp.FWD1002_BUFFER[1])
        self.assertEqual(cp.FWD1002_TRADES_HOURS, (cp.FIRST_BOOT_HOUR,))
        self.assertEqual(cp.GRANT_DAYS[0], cp.FIRST_BOOT_HOUR[:10])
        self.assertEqual(cp.WALK2_HOURS, ("2026-10-16T01", "2026-11-06T01"))
        # every later boot's forward-1002 creates stay inside the allowed range, and no boot after 10-17 reaches into it
        for day, expect_fwd in (("2026-10-16", True), ("2026-10-17", True), ("2026-10-18", False)):
            fwd_hours = [h for h in cp._staging_hours(cp._calendar_ms(day)) if h < cp.WALK2_HOURS[0]]
            self.assertEqual(bool([h for h in fwd_hours if h >= cp.FWD1002_BUFFER[0]]), expect_fwd, day)
            self.assertTrue(all(cp.FWD1002_CREATES_HOURS[0] <= h < cp.FWD1002_CREATES_HOURS[1] for h in fwd_hours), day)

    def test_constants_match_the_walker_and_the_final_ledger_writer(self) -> None:
        from tools import exp012_forward as ef
        from tools import forward_family as ff

        self.assertEqual(cp.FINAL_SCHEMA, ef.SCHEMA_MARKER)
        self.assertEqual(cp.FINAL_EXPERIMENT, ff.PRIMARY_EXPERIMENT)
        self.assertEqual(cp.FINAL_WINDOW, (ef.PINNED_CLEAN_CLOCK, ef.PINNED_READ_END))
        self.assertEqual(cp.FINAL_LEDGER, str(ef.DEFAULT_LEDGER))
        self.assertEqual(cp.FINAL_LEDGER, str(ff.PRIMARY_LEDGER))
        self.assertEqual((cp.WALK2_DIR, cp.FWD1002_DIR), (str(self.walk), str(self.fwd)))  # patched by setUp
        self.assertEqual(cp.GRANT_SUFFIX, ".jsonl.zst")

    # --- grant shape ------------------------------------------------------------------------------------------
    def test_grant_is_frozen_and_only_creates_and_trades(self) -> None:
        g = self.grant()
        with self.assertRaises(Exception):
            g.hours = {}  # frozen dataclass
        with self.assertRaises(TypeError):
            g.hours["2026-10-16T05"] = {}  # the map is read-only
        good = ("/x/y", "0" * 64)
        for bad_kind in ("events", "migrations", "pools"):
            with self.assertRaises(cp.Refused, msg=bad_kind):
                cp.ReadGrant({"2026-10-16T05": {bad_kind: good}})
        for bad in (("/x",), ("/x", "NOTHEX"), ("/x", "A" * 64), (1, "0" * 64)):
            with self.assertRaises(cp.Refused, msg=str(bad)):
                cp.ReadGrant({"2026-10-16T05": {"trades": bad}})
        with self.assertRaises(cp.Refused):
            cp.ReadGrant({"2026-10-16": {"trades": good}})
        with self.assertRaises(cp.Refused):
            cp.ReadGrant({"2026-10-16T05": {}})

    # --- allowed roots, layout, hour bounds ---------------------------------------------------------------------
    def test_paths_outside_the_two_roots_or_layout_refuse(self) -> None:
        sha = "0" * 64
        walk, fwd = str(self.walk), str(self.fwd)
        for path in (
            f"/data/mal/clean-view/explore-0814/w1/trades/trades-2026-10-16T05{cp.GRANT_SUFFIX}",  # an exploration root
            f"{walk}/events/events-2026-10-16T05{cp.GRANT_SUFFIX}",  # the --event-v stream
            f"{walk}/migrations/migrations-2026-10-16T05{cp.GRANT_SUFFIX}",
            f"{walk}/trades/trades-2026-10-16T06{cp.GRANT_SUFFIX}",  # names another hour
            f"{walk}/trades/../trades/trades-2026-10-16T05{cp.GRANT_SUFFIX}",  # not normalised
            f"{walk}/trades/trades-2026-10-16T05.jsonl",  # not sealed
            f"{walk}x/trades/trades-2026-10-16T05{cp.GRANT_SUFFIX}",  # a prefix of the root is not the root
            f"trades/trades-2026-10-16T05{cp.GRANT_SUFFIX}",
        ):
            with self.subTest(path=path), self.assertRaises(cp.Refused):
                self.run_grant(cp.ReadGrant({"2026-10-16T05": {"trades": (path, sha)}}))
        # the layout under a root is exact, so a root that names a forbidden word is still fine (the roots are exempt)
        # while a forbidden name below a root has no way in: events/ and migrations/ above are refused as layout
        self.assertEqual(cp._grant_root("2026-10-16T05", "trades", f"{fwd}/trades/trades-2026-10-16T05{cp.GRANT_SUFFIX}"), fwd)

    def test_hour_bounds(self) -> None:
        sha = "0" * 64
        walk, fwd = str(self.walk), str(self.fwd)

        def g(root: str, kind: str, hour: str) -> cp.ReadGrant:
            return cp.ReadGrant({hour: {kind: (f"{root}/{kind}/{kind}-{hour}{cp.GRANT_SUFFIX}", sha)}})

        allowed = [(walk, "trades", "2026-10-16T01"), (walk, "creates", "2026-10-16T01"), (walk, "trades", "2026-11-06T00"),
                   (fwd, "trades", "2026-10-16T00"), (fwd, "creates", "2026-10-16T00"), (fwd, "creates", "2026-10-14T21"),
                   (fwd, "creates", "2026-10-15T23")]
        for root, kind, hour in allowed:
            with self.subTest(allowed=(kind, hour)):
                cp._GrantSession(g(root, kind, hour), self.ledger)
        refused = [(walk, "trades", "2026-10-16T00"), (walk, "trades", "2026-11-06T01"), (walk, "creates", "2026-11-07T00"),
                   (walk, "trades", "2026-10-15T12"), (walk, "trades", "2026-09-20T00"),
                   (fwd, "trades", "2026-10-15T23"), (fwd, "trades", "2026-10-14T01"), (fwd, "trades", "2026-10-16T01"),
                   (fwd, "creates", "2026-10-14T20"), (fwd, "creates", "2026-10-14T01"), (fwd, "creates", "2026-10-16T01"),
                   (fwd, "creates", "2026-10-02T09")]
        for root, kind, hour in refused:
            with self.subTest(refused=(kind, hour)), self.assertRaises(cp.Refused):
                cp._GrantSession(g(root, kind, hour), self.ledger)

    def test_days_outside_the_walk_are_refused(self) -> None:
        for days in (["2026-10-15"], ["2026-11-07"], ["2026-10-16", "2026-10-02"], ["2026-10-1"], ["2026-08-15"]):
            with self.subTest(days=days), self.assertRaises(cp.Refused):
                self.run_grant(days=days)

    def test_grant_mode_pins_the_e0_verified_settings(self) -> None:
        for kw in ({"roots": [str(self.walk)]}, {"daily_restart": False}, {"prune_every": 1}, {"create_time": "row"}):
            with self.subTest(kw=kw), self.assertRaises(cp.Refused):
                self.run_grant(**kw)

    # --- the hash --------------------------------------------------------------------------------------------
    def test_hash_mismatch_refuses_before_a_row_is_fed(self) -> None:
        g = self.grant()
        f = self.walk / "trades" / "trades-2026-10-16T02.jsonl.zst"
        original = f.read_bytes()
        f.write_bytes(original + b"\x00")  # any change to the stored bytes
        with self.assertRaises(cp.Refused) as ctx:
            self.run_grant(g)
        self.assertIn("sha256", str(ctx.exception))
        self.assertIn(str(f), str(ctx.exception))

    def test_creates_hash_is_checked_in_boot_staging_too(self) -> None:
        g = self.grant()
        f = self.fwd / "creates" / "creates-2026-10-15T23.jsonl.zst"
        f.write_bytes(f.read_bytes()[:-1])
        with self.assertRaises(cp.Refused) as ctx:
            self.run_grant(g)
        self.assertIn("sha256", str(ctx.exception))

    def test_a_symlink_is_refused_even_with_the_right_hash(self) -> None:
        g = self.grant()
        f = self.walk / "trades" / "trades-2026-10-16T02.jsonl.zst"
        real = self.dir / "elsewhere.zst"
        shutil.move(str(f), str(real))
        f.symlink_to(real)
        with self.assertRaises(cp.Refused):
            self.run_grant(g)

    def test_an_hour_or_kind_not_in_the_grant_refuses(self) -> None:
        sess = cp._GrantSession(self.grant(drop=[("2026-10-16T02", "trades")]), self.ledger)
        sess.attach(self.dir / "t")
        for hour, kind in (("2026-10-16T02", "trades"), ("2026-10-16T09", "creates"), ("2026-10-16T02", "events"), ("2026-10-16T02", "migrations")):
            with self.subTest(hour=hour, kind=kind), self.assertRaises(cp.Refused):
                sess.copy_verified(hour, kind, self.dir / "t" / "x")
        self.assertFalse((self.dir / "t" / "x").exists())

    def test_a_replayed_hour_not_in_the_grant_is_skipped_and_never_opened(self) -> None:
        recs, meta = self.run_grant(self.grant(drop=[("2026-10-16T02", "trades")]))
        self.assertNotIn("Hi", {r["mint"] for r in recs})
        g = meta["grant"]
        self.assertIn("2026-10-16T02", g["replayed_hours_not_in_grant"])
        self.assertNotIn(("2026-10-16T02", "trades"), {(h, k) for h, k, _ in g["opened"]})
        self.assertNotIn("2026-10-16T02", [h for h, k, _ in g["opened"] if k == "trades"])

    # --- forward-1002 needs the FINAL ---------------------------------------------------------------------------
    def test_forward_1002_refuses_without_a_final_and_opens_nothing(self) -> None:
        secret = "SENTINEL-0123"

        def marker(**over) -> dict:
            m = {"final": True, "schema": cp.FINAL_SCHEMA, "experiment": "EXP-012", "test_window": False, "clean_clock": cp.FINAL_WINDOW[0],
                 "read_end": cp.FINAL_WINDOW[1], "note": secret}
            m.update(over)
            return m

        no_test_key = marker()
        del no_test_key["test_window"]
        cases = {
            "missing": None,
            "empty": "",
            "no_final_row": json.dumps(marker(final=False)) + "\n",
            "final_is_a_string": json.dumps(marker(final="true")) + "\n",
            "test_window_final": json.dumps(marker(test_window=True)) + "\n",
            "test_window_key_absent": json.dumps(no_test_key) + "\n",
            "other_window": json.dumps(marker(clean_clock="2026-10-05T05:00:00Z")) + "\n",
            "other_experiment": json.dumps(marker(experiment="EXP-099")) + "\n",
            "other_schema": json.dumps(marker(schema="x")) + "\n",
            "torn_line": json.dumps(marker()),
            "not_json": "{nope}\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                ledger = self.dir / "cases" / f"{name}.jsonl"
                ledger.parent.mkdir(exist_ok=True)
                if text is not None:
                    ledger.write_text(text)
                with mock.patch.object(cp._GrantSession, "copy_verified", side_effect=AssertionError("opened a file")), \
                        self.assertRaises(cp.Refused) as ctx:
                    self.run_grant(ledger=ledger)
                self.assertNotIn(secret, str(ctx.exception))
        ok = self.dir / "cases" / "ok.jsonl"
        ok.write_text(json.dumps({"x": 1}) + "\n" + json.dumps(marker()) + "\n")
        cp.require_final(ok)  # a FINAL row among others is found

    def test_a_real_final_marker_opens_forward_1002_and_walk_2_alone_needs_no_ledger(self) -> None:
        self.run_grant()  # the setUp ledger holds a marker written by exp012_forward._final_docs
        walk_only = cp.ReadGrant({h: {k: v for k, v in kinds.items()} for h, kinds in self.grant().hours.items() if h >= "2026-10-16T01"})
        recs, meta = self.run_grant(walk_only, ledger=self.dir / "does-not-exist.jsonl")
        self.assertFalse(meta["grant"]["forward_1002_granted"])
        self.assertTrue(recs)

    def test_a_forbidden_name_in_a_root_is_exempt_but_the_cutoff_still_applies_without_a_grant(self) -> None:
        heart = self.dir / "heartbeat-positions"  # FORBIDDEN_NAMES words in the root path itself
        shutil.copytree(self.walk, heart)
        sha = hashlib.sha256((heart / "trades" / "trades-2026-10-16T02.jsonl.zst").read_bytes()).hexdigest()
        with mock.patch.object(cp, "WALK2_DIR", str(heart)):
            sess = cp._GrantSession(cp.ReadGrant({"2026-10-16T02": {"trades": (f"{heart}/trades/trades-2026-10-16T02.jsonl.zst", sha)}}), self.ledger)
            sess.attach(self.dir / "t2")
            self.assertEqual(sess.copy_verified("2026-10-16T02", "trades", self.dir / "t2" / "copy"), sha)
        # without a grant the exploration refusals are unchanged for both roots
        for root in (cp.WALK2_DIR, cp.FWD1002_DIR):
            with self.assertRaises(cp.Refused):
                cp.refuse_path(f"{root}/trades/trades-2026-10-16T05.jsonl.zst", [root])
            with self.assertRaises(cp.Refused):
                cp.replay_view(cp.Block("t", (root,), ".jsonl.zst", False), ["2026-10-16"], engine=self.engine(), roots=[root])

    # --- strict lines apply in grant mode -------------------------------------------------------------------------
    def test_bad_lines_refuse_in_grant_mode(self) -> None:
        from tools.pump_history_backfill import seal_jsonl

        for kind, root, hour in (("trades", self.walk, "2026-10-16T02"), ("creates", self.fwd, "2026-10-15T23"), ("creates", self.walk, "2026-10-16T23")):
            with self.subTest(kind=kind, hour=hour):
                f = root / kind / f"{kind}-{hour}.jsonl.zst"
                good = f.read_bytes()
                subprocess.run(["zstd", "-q", "-d", "-f", str(f), "-o", str(f.with_suffix(""))], check=True)
                plain = f.with_suffix("")
                plain.write_bytes(plain.read_bytes() + b'{"broken":\n')
                seal_jsonl(plain)
                try:
                    with self.assertRaises(cp.BadLineRefused) as ctx:
                        self.run_grant()  # the grant is built from the changed file, so the hash is right; the lines are not
                    self.assertEqual(ctx.exception.kind, "not_json")
                    self.assertIn(str(f), str(ctx.exception))
                finally:
                    f.write_bytes(good)


class EventVTests(_Base):
    """Walk 2 runs the walker with --event-v: trade rows carry V, ix_name, creator_fee_unclaimed, buyback_fee and
    fee_recipient_zero, and an events/ stream is written next to creates/ and trades/. The gate sees none of it."""

    def test_event_v_keys_are_the_decoders(self) -> None:
        from observe.trade_decode import EVENT_V_KEYS

        self.assertEqual(set(EVENT_V_ROW_KEYS), set(EVENT_V_KEYS))

    def _decisions(self, extra: dict | None, *, with_events_dir: bool = False):
        from tools.pump_history_backfill import seal_jsonl

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            t = DAY0 + 2 * HOUR
            hour = cp._hour_of_ms(t)
            rows = _mint_rows("Hi", 7, t0=t) + _mint_rows("Lo", 1, t0=t + 30_000)
            if extra is not None:
                for r in rows:
                    r.update(extra)
            _write_hour(root, "creates", hour, _lines([_crow("Hi", t, "C1"), _crow("Lo", t + 30_000, "C2")]), zst=True)
            _write_hour(root, "trades", hour, _lines(sorted(rows, key=lambda r: r["t_recv_ms"])), zst=True)
            if with_events_dir:  # an events stream the replay must never open: garbage that any reader would refuse
                (root / "events").mkdir()
                ev = root / "events" / f"events-{hour}.jsonl"
                ev.write_bytes(b'{"type":"boost"}\n\x00\x00 not json\n[1]\n')
                seal_jsonl(ev)
            blk = cp.Block("t", (td,), ".jsonl.zst", False)
            opened: list[str] = []
            real = cp.iter_lines
            with mock.patch.object(cp, "iter_lines", lambda p, r: (opened.append(str(p)), real(p, r))[1]):
                recs, meta = cp.replay_view(blk, [cp._day_of_ms(DAY0)], engine=self.engine(), roots=[td])
            return recs, meta, opened

    def test_event_v_fields_do_not_change_a_decision(self) -> None:
        if shutil.which("zstd") is None:
            self.skipTest("zstd binary not available")
        plain, meta0, _ = self._decisions(None)
        withv, meta1, _ = self._decisions(EVENT_V_ROW_KEYS)
        self.assertEqual({r["mint"]: r["decision"] for r in plain}, {"Hi": "pick", "Lo": "below"})
        self.assertEqual(canonical(plain, meta0), canonical(withv, meta1))  # scores included, to the last digit
        # a different V on every row changes nothing either: features never see V
        other, meta2, _ = self._decisions({**EVENT_V_ROW_KEYS, "virtual_quote_reserves": -5, "fee_recipient_zero": True, "ix_name": "sell"})
        self.assertEqual(canonical(plain, meta0), canonical(other, meta2))

    def test_the_events_dir_is_never_read(self) -> None:
        if shutil.which("zstd") is None:
            self.skipTest("zstd binary not available")
        plain, meta0, _ = self._decisions(EVENT_V_ROW_KEYS)
        withdir, meta1, opened = self._decisions(EVENT_V_ROW_KEYS, with_events_dir=True)
        self.assertEqual(canonical(plain, meta0), canonical(withdir, meta1))
        self.assertTrue(opened and not any("events" in Path(p).parts[-2] for p in opened))

    def test_grant_mode_with_event_v_rows_and_an_events_dir(self) -> None:
        if shutil.which("zstd") is None:
            self.skipTest("zstd binary not available")
        with tempfile.TemporaryDirectory() as td:
            walk, fwd = Path(td) / "walk2", Path(td) / "fwd"
            days = walk2_fixture(walk, fwd, event_v=True)
            (walk / "events").mkdir()
            (walk / "events" / "events-2026-10-16T02.jsonl.zst").write_bytes(b"\x00not zstd")
            hours: dict[str, dict] = {}
            for root in (walk, fwd):
                for kind in cp.GRANT_KINDS:
                    for f in sorted((root / kind).iterdir()):
                        hour = f.name[len(kind) + 1: -len(".jsonl.zst")]
                        hours.setdefault(hour, {})[kind] = (str(f), hashlib.sha256(f.read_bytes()).hexdigest())
            ledger = Path(td) / "FINAL_READS.jsonl"
            ledger.write_text(json.dumps({"final": True, "schema": cp.FINAL_SCHEMA, "experiment": "EXP-012", "test_window": False,
                                          "clean_clock": cp.FINAL_WINDOW[0], "read_end": cp.FINAL_WINDOW[1]}) + "\n")
            with mock.patch.object(cp, "WALK2_DIR", str(walk)), mock.patch.object(cp, "FWD1002_DIR", str(fwd)):
                recs, meta = cp.replay_view(cp.WALK2_BLOCK, days, engine=self.engine(), grant=cp.ReadGrant(hours), _test_final_ledger=ledger)
            self.assertEqual({r["mint"]: r["decision"] for r in recs if r["kind"] == "decision"},
                             {"E0m": "pick", "Hi": "pick", "Lo": "below", "D1Hi": "pick", "D1Lo": "below"})
            self.assertFalse([h for h, k, _ in meta["grant"]["opened"] if k not in ("creates", "trades")])
            # the events/ file is not in the grant and cannot be put in it
            with self.assertRaises(cp.Refused):
                cp.ReadGrant({"2026-10-16T02": {"events": (str(walk / "events" / "events-2026-10-16T02.jsonl.zst"), "0" * 64)}})


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
