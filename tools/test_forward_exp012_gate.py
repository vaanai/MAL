"""Tests for the EXP-012 gate in the forward-paper runner. Fixtures only: a tiny
LightGBM model trained here, synthetic rows from tools/test_forward_paper.py's
helpers. No holdout, no host path."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from tools.exploration_entry_model import (
    FEATURE_NAMES as SOURCE_NAMES,
    _Feat,
    causal_events,
    compute_features,
    count_prior_creates,
)
from tools.forward_exp012_gate import Exp012Online, load_gate
from tools.forward_paper import (
    BookSpec,
    RiskConfigError,
    adopt_book_limits,
    books_from_config,
    replay_rows,
    ForwardEngine,
)
from tools.latency_curve import WINDOW_MS
from tools.test_forward_paper import B0, Q0, T0, _create, _trade

REPO = Path(__file__).resolve().parent.parent
FROZEN_FEATURES = REPO / "ARTIFACTS" / "exp012" / "features.json"
NAMES = list(json.loads(FROZEN_FEATURES.read_text(encoding="utf-8"))["frozen_feature_names"])
END = T0 + 3 * 3_600_000


def _make_model(dirpath: Path) -> tuple[Path, str, Path]:
    """18-feature model: score is high iff n_buys >= 4. Returns (model, md5, features.json)."""
    import lightgbm as lgb
    import numpy as np

    rng = np.random.RandomState(1)
    x = rng.rand(400, len(NAMES))
    x[:, NAMES.index("n_buys")] = rng.randint(0, 10, size=400)
    y = (x[:, NAMES.index("n_buys")] >= 4).astype(int)
    ds = lgb.Dataset(x, label=y, feature_name=NAMES, free_raw_data=False)
    booster = lgb.train(
        {"objective": "binary", "verbose": -1, "num_threads": 1, "min_data_in_leaf": 5, "seed": 1, "deterministic": True},
        ds,
        num_boost_round=20,
    )
    model = dirpath / "model.txt"
    booster.save_model(str(model))
    feats = dirpath / "features.json"
    feats.write_text(json.dumps({"frozen_feature_names": NAMES}), encoding="utf-8")
    return model, hashlib.md5(model.read_bytes()).hexdigest(), feats


def _gated(model: Path, md5: str, feats: Path, thr: float = 0.5, book_id: str = "migrate_exp012") -> BookSpec:
    return BookSpec(
        book_id, "migrate", "tp50_sl30", creator_cooldown_ms=0, token_cooldown_ms=0,
        entry_model=str(model), entry_model_md5=md5, entry_threshold=thr, entry_features=str(feats),
    )


def _cfg(model: Path, md5: str, feats: Path) -> dict:
    return {"books": [{"id": "m", "kind": "migrate", "entry_model": str(model), "entry_model_md5": md5, "entry_threshold": 0.5, "entry_features": str(feats)}]}


def _mint_rows(mint: str, n_buys: int, *, t0: int = T0, mig_after_ms: int = 20_000) -> list[dict]:
    rows = []
    for i in range(n_buys):
        side = "sell" if i == 3 else "buy"
        rows.append(
            _trade(
                mint, t0 + 1_000 + 700 * i, side=side, sol=500_000_000 + 10_000_000 * i, token=1_000_000 + i,
                quote=Q0 + 1_000_000_000 * i, trader=f"w{i % 3}", slot=10 + i, event_index=1,
            )
        )
    rows.append(_trade(mint, t0 + mig_after_ms, venue="pumpswap", trader="pool", quote=70_000_000_000, base=B0 // 2, slot=100, event_index=1))
    rows.append(_trade(mint, t0 + mig_after_ms + 5_000, trader="after", slot=101, event_index=1))
    return rows


class GateConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.model, self.md5, self.feats = _make_model(self.dir)

    def test_valid_config_loads_and_plain_book_has_no_gate(self) -> None:
        cfg = _cfg(self.model, self.md5, self.feats)
        cfg["books"].append({"id": "plain", "kind": "migrate", "exit": "hold_30s"})
        books = books_from_config(cfg)
        self.assertEqual(books[0].entry_model, str(self.model))
        self.assertIsNone(books[1].entry_model)
        self.assertIsNone(books[1].entry_threshold)

    def test_md5_mismatch_refuses(self) -> None:
        with self.assertRaises(RiskConfigError) as cm:
            books_from_config(_cfg(self.model, "0" * 32, self.feats))
        self.assertIn("md5", str(cm.exception))

    def test_feature_order_mismatch_refuses(self) -> None:
        swapped = list(NAMES)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        bad = self.dir / "bad_features.json"
        bad.write_text(json.dumps({"frozen_feature_names": swapped}), encoding="utf-8")
        with self.assertRaises(RiskConfigError) as cm:
            books_from_config(_cfg(self.model, self.md5, bad))
        self.assertIn("feature", str(cm.exception))

    def test_partial_or_wrong_kind_refuses(self) -> None:
        with self.assertRaises(RiskConfigError):
            books_from_config({"books": [{"id": "m", "kind": "migrate", "entry_model": str(self.model)}]})
        cfg = _cfg(self.model, self.md5, self.feats)
        cfg["books"][0]["kind"] = "baseline"
        with self.assertRaises(RiskConfigError):
            books_from_config(cfg)

    def test_engine_refuses_if_file_changes_after_load(self) -> None:
        spec = _gated(self.model, self.md5, self.feats)
        self.model.write_text(self.model.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            ForwardEngine([spec], kill_file=self.dir / "KILL")

    def test_no_hot_reload_of_gate_keys(self) -> None:
        spec = _gated(self.model, self.md5, self.feats, thr=0.5)
        engine = ForwardEngine([spec], kill_file=self.dir / "KILL")
        incoming = books_from_config(_cfg(self.model, self.md5, self.feats))
        incoming[0] = BookSpec(**{**incoming[0].__dict__, "book_id": spec.book_id, "entry_threshold": 0.01})
        adopt_book_limits(engine, incoming)
        self.assertEqual(engine.books[0].spec.entry_threshold, 0.5)


class GateDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.model, self.md5, self.feats = _make_model(self.dir)

    def _run(self, books: list[BookSpec], creates, rows, end: int = T0 + 120_000):
        return replay_rows(creates, rows, books, tape_end_ms=end, kill_file=self.dir / "KILL", offsets_ms=(5_000,))

    def test_enters_only_above_threshold(self) -> None:
        creates = [_create("Hi", T0, "CH"), _create("Lo", T0, "CL")]
        rows = _mint_rows("Hi", 7) + _mint_rows("Lo", 1)
        rows.sort(key=lambda r: r["t_recv_ms"])
        engine = self._run([_gated(self.model, self.md5, self.feats)], creates, rows)
        by = {r["mint"]: r for r in engine.exp012_rows}
        self.assertTrue(by["Hi"]["entered"])
        self.assertFalse(by["Lo"]["entered"])
        self.assertGreaterEqual(by["Hi"]["score"], 0.5)
        self.assertLess(by["Lo"]["score"], 0.5)
        self.assertEqual(by["Hi"]["threshold"], 0.5)
        self.assertEqual(list(by["Hi"]["features"]), NAMES)
        lo = [d for d in engine.decisions if d["mint"] == "Lo"]
        self.assertTrue(lo and all(d["action"] == "skip" and d["reason"] == "below_threshold" for d in lo))
        self.assertFalse([p for p in engine.positions if p["mint"] == "Lo"])
        self.assertTrue([p for p in engine.positions if p["mint"] == "Hi"])
        hi_decisions = [d for d in engine.decisions if d["mint"] == "Hi"]
        self.assertTrue(hi_decisions and all(d["reason"] != "below_threshold" for d in hi_decisions))
        self.assertTrue(all(d["score"] == by["Hi"]["score"] for d in hi_decisions if d["score"] is not None))
        self.assertEqual(engine.exp012.acc, {})  # accumulators dropped at the decision

    def test_ungated_book_rows_carry_no_gate_and_engine_has_no_state(self) -> None:
        creates = [_create("Hi", T0, "CH")]
        engine = self._run([BookSpec("m", "migrate", "tp50_sl30", creator_cooldown_ms=0, token_cooldown_ms=0)], creates, _mint_rows("Hi", 7))
        self.assertIsNone(engine.exp012)
        self.assertEqual(engine.exp012_rows, [])
        self.assertTrue(engine.decisions)

    def test_gated_and_ungated_book_side_by_side_ungated_unchanged(self) -> None:
        creates = [_create("Hi", T0, "CH"), _create("Lo", T0, "CL")]
        rows = sorted(_mint_rows("Hi", 7) + _mint_rows("Lo", 1), key=lambda r: r["t_recv_ms"])
        plain = BookSpec("plain", "migrate", "tp50_sl30", creator_cooldown_ms=0, token_cooldown_ms=0)
        solo = self._run([plain], creates, rows)
        both = self._run([plain, _gated(self.model, self.md5, self.feats)], creates, rows)
        pick = lambda e: [d for d in e.decisions if d["book"] == "plain"]
        self.assertEqual(pick(solo), pick(both))
        self.assertEqual([p for p in solo.positions if p["book"] == "plain"], [p for p in both.positions if p["book"] == "plain"])

    def test_migration_without_bonding_prints_is_skipped(self) -> None:
        creates = [_create("Nb", T0, "CN")]
        rows = [_trade("Nb", T0 + 5_000, venue="pumpswap", trader="p", quote=70_000_000_000, base=B0 // 2, slot=9, event_index=1)]
        engine = self._run([_gated(self.model, self.md5, self.feats)], creates, rows)
        self.assertEqual([r["reason"] for r in engine.exp012_rows], ["no_bond_history"])
        self.assertIsNone(engine.exp012_rows[0]["score"])


def _chain(ms: int) -> int:
    return (ms // 1000) * 1000


def _offline(mint_rows: list[dict], *, creator: str, create_ms: int, first_price: float, mig_ms: int, hist: dict[str, list[int]], keep_until_ms: int | None = None) -> dict[str, float]:
    """The offline builder's path on the same rows. In the getBlock walk t_recv_ms is None
    and every time is block_time * 1000, so each row's time is event_ts * 1000 here.
    Record bonding rows while the mint has not migrated (file order), then
    causal_events / compute_features."""
    feat = _Feat(creator, create_ms, first_price)
    for r in mint_rows:
        if r.get("venue") == "pumpswap":
            break
        t = r["event_ts"] * 1000
        if keep_until_ms is not None and t >= keep_until_ms:
            continue
        if r.get("venue") == "pump_bonding":
            feat.record(r, t)
    prior = count_prior_creates(hist, creator, create_ms)
    return compute_features(causal_events(feat.events, mig_ms), create_ms=create_ms, first_price=first_price, mig_ms=mig_ms, creator_prior_mints_24h=prior)


def _replay_chain(creates, rows, book, end, kill, chain_creates: dict[str, int] | None = None):
    """replay_rows with the create chain times set first (replay_rows has no such hook)."""
    from tools.forward_paper import LatencyMeter, _event_ts, flow_from_tape_row

    engine = ForwardEngine([book] if isinstance(book, BookSpec) else list(book), kill_file=kill, latency=LatencyMeter(), offsets_ms=(5_000,), tape_end_ms=end, retain_rows=True)
    engine.exp012_create_chain.update(chain_creates or {})
    for c in creates:
        engine.push_create(c)
    for row in rows:
        parsed = flow_from_tape_row(row)
        if parsed is None:
            continue
        mint, pr = parsed
        engine.push_print(mint, pr, _event_ts(row))
    engine.drain_until(end, final=True)
    return engine


class ParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.model, self.md5, self.feats = _make_model(self.dir)

    def _online(self, creates, rows, end, chain=None):
        engine = _replay_chain(creates, rows, _gated(self.model, self.md5, self.feats), end, self.dir / "KILL", chain)
        return {r["mint"]: r for r in engine.exp012_rows}

    def _assert_equal(self, got: dict[str, float], want: dict[str, float]) -> None:
        for n in NAMES:
            self.assertTrue(math.isclose(got[n], want[n], rel_tol=1e-12, abs_tol=1e-12), (n, got[n], want[n]))

    def test_online_features_equal_offline_chain_time(self) -> None:
        rows = _mint_rows("P", 8)
        # Same second as the migration (event_ts equal), earlier receive time: at second
        # resolution t == mig_ms, so it is excluded exactly as offline excludes it.
        same_sec = _trade("P", T0 + 19_900, trader="sameSec", sol=9_000_000_000, slot=99, event_index=0)
        self.assertEqual(same_sec["event_ts"], (T0 + 20_000) // 1000)
        rows.insert(-2, same_sec)
        rows.sort(key=lambda r: r["t_recv_ms"])
        creates = [
            _create("Old1", T0 - 3_600_000, "CP"),
            _create("Old2", T0 - 7_200_000, "CP"),
            _create("TooOld", T0 - 25 * 3_600_000, "CP"),
            _create("Other", T0 - 100_000, "CZ"),
            _create("P", T0, "CP"),
        ]
        chain = {"Old1": _chain(T0 - 3_600_000), "Old2": _chain(T0 - 7_200_000), "TooOld": _chain(T0 - 25 * 3_600_000), "Other": _chain(T0 - 100_000), "P": _chain(T0)}
        row = self._online(creates, rows, END, chain)["P"]
        hist = {"CP": sorted(chain[m] for m in ("Old1", "Old2", "TooOld", "P")), "CZ": [chain["Other"]]}
        first_price = 35.0 / 1_073_000_000.0
        mig_ms = ((T0 + 20_000) // 1000) * 1000
        want = _offline(rows, creator="CP", create_ms=chain["P"], first_price=first_price, mig_ms=mig_ms, hist=hist)
        self.assertEqual(want["creator_prior_mints_24h"], 2.0)
        self.assertEqual(want["n_buys"], 7.0)  # the same-second print is not counted
        self.assertEqual(want["time_to_migrate_s"], 20.0)
        self.assertGreater(want["sniper_buy_share"], 0.0)
        self.assertLess(want["sniper_buy_share"], 1.0)
        self._assert_equal(row["features"], {n: want[n] for n in NAMES})
        self.assertEqual(row["mig_ms"], mig_ms)
        self.assertEqual(row["time_fallbacks"], {"prints": 0, "create": False, "migration": False})

    def test_fallback_to_recv_time_is_counted(self) -> None:
        rows = _mint_rows("F", 5)
        for r in rows:
            r.pop("event_ts", None)
        row = self._online([_create("F", T0, "CF")], rows, END)["F"]
        fb = row["time_fallbacks"]
        self.assertEqual(fb["prints"], 5)
        self.assertTrue(fb["create"])
        self.assertTrue(fb["migration"])
        self.assertEqual(row["mig_ms"], T0 + 20_000)  # receive time, only because event_ts is missing
        self.assertEqual(row["features"]["time_to_migrate_s"], 20.0)

    def test_late_bonding_print_before_mig_is_counted(self) -> None:
        rows = _mint_rows("L", 5)
        mig = next(r for r in rows if r["venue"] == "pumpswap")
        # Delivered after the pumpswap print (same receive ms, later slot) but chain-time earlier.
        late = _trade("L", mig["t_recv_ms"], trader="late", sol=2_000_000_000, slot=mig["slot"] + 1, event_index=1)
        late["event_ts"] = mig["event_ts"] - 1
        rows.append(late)
        creates = [_create("L", T0, "CL")]
        row = self._online(creates, rows, END, {"L": _chain(T0)})["L"]
        self.assertEqual(row["features"]["n_buys"], 4.0 + 1.0)  # 4 buys + 1 sell among 5 early prints, plus the late buy
        self.assertEqual(row["features"]["n_bonding_trades"], 6.0)

    def test_truncation_quirk_is_documented_and_tested(self) -> None:
        """A mint that migrates after 32 min: online stops recording at create + 32 min
        exactly (chain time). Offline keeps recording until its next flush, so it can see
        later prints. Both numbers are asserted, so the divergence is pinned."""
        rows = [r for r in _mint_rows("T", 4) if r["venue"] == "pump_bonding" and r["t_recv_ms"] < T0 + 10_000]
        late_in = _trade("T", T0 + WINDOW_MS - 2_000, trader="lateA", sol=1_000_000_000, slot=200, event_index=1)
        late_out = _trade("T", T0 + WINDOW_MS + 60_000, trader="lateB", sol=3_000_000_000, slot=300, event_index=1)
        mig = _trade("T", T0 + WINDOW_MS + 120_000, venue="pumpswap", trader="pool", quote=70_000_000_000, base=B0 // 2, slot=400, event_index=1)
        rows += [late_in, late_out, mig]
        rows.sort(key=lambda r: r["t_recv_ms"])
        online = self._online([_create("T", T0, "CT")], rows, T0 + 2 * WINDOW_MS, {"T": _chain(T0)})["T"]["features"]
        fp = 35.0 / 1_073_000_000.0
        mig_ms = mig["event_ts"] * 1000
        hist = {"CT": [_chain(T0)]}
        flushed_at_32 = _offline(rows, creator="CT", create_ms=_chain(T0), first_price=fp, mig_ms=mig_ms, hist=hist, keep_until_ms=_chain(T0) + WINDOW_MS)
        never_flushed = _offline(rows, creator="CT", create_ms=_chain(T0), first_price=fp, mig_ms=mig_ms, hist=hist)
        self._assert_equal(online, {n: flushed_at_32[n] for n in NAMES})
        self.assertGreater(never_flushed["n_buys"], online["n_buys"])  # offline can differ here
        self.assertGreater(never_flushed["buy_sol"], online["buy_sol"])

    def test_missing_price_sol_is_a_documented_difference(self) -> None:
        rows = _mint_rows("Q", 5)
        for r in rows:
            r.pop("price_sol", None)
        online = self._online([_create("Q", T0, "CQ")], rows, END, {"Q": _chain(T0)})["Q"]["features"]
        fp = 35.0 / 1_073_000_000.0
        offline = _offline(rows, creator="CQ", create_ms=_chain(T0), first_price=fp, mig_ms=_chain(T0 + 20_000), hist={"CQ": [_chain(T0)]})
        # offline records no price (stays at first price): return is 0. Online derives one from reserves.
        self.assertEqual(offline["price_return_pre"], 0.0)
        self.assertNotEqual(online["price_return_pre"], 0.0)


class HotPathSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.model, self.md5, self.feats = _make_model(self.dir)

    def _mixed(self, creates, rows):
        plain = BookSpec("plain", "migrate", "tp50_sl30", creator_cooldown_ms=0, token_cooldown_ms=0)
        return plain, _replay_chain(creates, rows, [plain, _gated(self.model, self.md5, self.feats)], T0 + 120_000, self.dir / "KILL")

    def _baseline(self, creates, rows):
        plain = BookSpec("plain", "migrate", "tp50_sl30", creator_cooldown_ms=0, token_cooldown_ms=0)
        return _replay_chain(creates, rows, [plain], T0 + 120_000, self.dir / "KILL")

    def test_unknown_feature_name_refuses_at_load(self) -> None:
        bad_names = NAMES[:-1] + ["not_a_feature"]
        bad = self.dir / "bad_names.json"
        bad.write_text(json.dumps({"frozen_feature_names": bad_names}), encoding="utf-8")
        from tools.forward_exp012_gate import GateConfigError

        with self.assertRaises(GateConfigError) as cm:
            load_gate(str(self.model), self.md5, 0.5, str(bad))
        self.assertIn("not_a_feature", str(cm.exception))

    def test_score_raising_gives_gate_error_and_ungated_book_is_unaffected(self) -> None:
        creates = [_create("Hi", T0, "CH")]
        rows = _mint_rows("Hi", 7)
        base = self._baseline(creates, rows)
        orig = load_gate(str(self.model), self.md5, 0.5, str(self.feats)).__class__.score
        try:
            from tools.forward_exp012_gate import GateSpec

            def boom(self, feats):
                raise ValueError("boom")

            GateSpec.score = boom
            plain, engine = self._mixed(creates, rows)
        finally:
            GateSpec.score = orig
        gate_rows = engine.exp012_rows
        self.assertEqual(len(gate_rows), 1)
        self.assertEqual(gate_rows[0]["reason"], "gate_error")
        self.assertEqual(gate_rows[0]["error"], "ValueError")
        self.assertFalse(gate_rows[0]["entered"])
        gated = [d for d in engine.decisions if d["book"] == "migrate_exp012"]
        self.assertTrue(gated and all(d["reason"] == "gate_error" for d in gated))
        self.assertFalse([p for p in engine.positions if p["book"] == "migrate_exp012"])
        keep = lambda e: [d for d in e.decisions if d["book"] == "plain"]
        self.assertEqual(keep(engine), keep(base))
        self.assertEqual([p for p in engine.positions if p["book"] == "plain"], [p for p in base.positions if p["book"] == "plain"])

    def test_feature_computation_raising_is_a_gate_error_too(self) -> None:
        creates = [_create("Hi", T0, "CH")]
        rows = _mint_rows("Hi", 7)
        base = self._baseline(creates, rows)
        orig = Exp012Online.features_at

        def boom(self, mint, fallback):
            raise KeyError("x")

        Exp012Online.features_at = boom
        try:
            plain, engine = self._mixed(creates, rows)
        finally:
            Exp012Online.features_at = orig
        self.assertEqual(engine.exp012_rows[0]["error"], "KeyError")
        self.assertEqual([d for d in engine.decisions if d["book"] == "plain"], [d for d in base.decisions if d["book"] == "plain"])

    def test_feed_hook_error_is_contained(self) -> None:
        acc = Exp012Online()
        acc.note_create("m", "c", 1_700_000_000_000, 1.0)
        acc.acc["m"].feat = None  # makes record() raise
        acc.note_print("m", venue="pump_bonding", t_recv_ms=1, event_ts=1_700_000_001, side="buy", trader="t", sol_lamports=1, token_raw=1, price_sol=1.0)
        self.assertEqual(acc.features_at("m", 0), (None, "gate_error"))

    def test_gate_module_not_imported_without_gate_keys(self) -> None:
        import subprocess
        import sys

        code = (
            "import sys; import tools.forward_paper as f; "
            "b = f.books_from_config({'books': [{'id': 'm', 'kind': 'migrate'}]}); "
            "e = f.ForwardEngine(b, kill_file=__import__('pathlib').Path('/nonexistent/KILL')); "
            "assert e.exp012 is None; assert 'tools.forward_exp012_gate' not in sys.modules"
        )
        res = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)


class ReloadVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.model, self.md5, self.feats = _make_model(self.dir)
        self.cfg = self.dir / "cfg.json"

    def _write(self, doc: dict) -> None:
        self.cfg.write_text(json.dumps(doc), encoding="utf-8")

    def test_refused_gate_reload_and_removed_keys_are_visible(self) -> None:
        from tools.forward_paper import emit_config_events, reload_risk_config_ex

        current = books_from_config(_cfg(self.model, self.md5, self.feats))
        # 1. gate md5 now wrong: refused, flagged as a gate error, running books kept
        self._write(_cfg(self.model, "0" * 32, self.feats))
        kept, _k, ev = reload_risk_config_ex(self.cfg, current)
        self.assertIs(kept, current)
        self.assertEqual([e["event"] for e in ev], ["config_reload_refused"])
        self.assertTrue(ev[0]["gate_error"])
        # 2. gate keys removed from the file: accepted for other keys, gate keys ignored, recorded
        self._write({"books": [{"id": "m", "kind": "migrate"}]})
        books, _k, ev = reload_risk_config_ex(self.cfg, current)
        self.assertEqual([e["event"] for e in ev], ["gate_keys_removed_ignored"])
        self.assertEqual(ev[0]["book"], "m")
        engine = ForwardEngine(current, kill_file=self.dir / "KILL")
        adopt_book_limits(engine, books)
        self.assertEqual(engine.books[0].spec.entry_model, str(self.model))  # still gated
        # 3. the WARN line and the decisions-log record
        class Log:
            rows: list = []

            def write(self, row):
                self.rows.append(row)

        log = Log()
        log.rows = []
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            emit_config_events(ev, log, 123)
        self.assertIn("WARN", buf.getvalue())
        self.assertEqual(log.rows[0]["event"], "gate_keys_removed_ignored")
        self.assertEqual(log.rows[0]["ts_ms"], 123)

    def test_non_gate_refusal_is_not_flagged_as_gate_error(self) -> None:
        from tools.forward_paper import reload_risk_config_ex

        current = books_from_config(_cfg(self.model, self.md5, self.feats))
        self._write({"books": [{"id": "m", "kind": "migrate", "size_sol": 5}]})
        _kept, _k, ev = reload_risk_config_ex(self.cfg, current)
        self.assertEqual(ev[0]["event"], "config_reload_refused")
        self.assertFalse(ev[0]["gate_error"])


class AccumulatorBoundTests(unittest.TestCase):
    def test_unmigrated_mint_dropped_after_60_min_and_history_trimmed(self) -> None:
        acc = Exp012Online()
        acc.note_create("m", "c", 1_000, 1.0)
        acc.prune(1_000 + 59 * 60_000)
        self.assertIn("m", acc.acc)
        acc.prune(1_000 + 61 * 60_000)
        self.assertNotIn("m", acc.acc)
        acc.prune(1_000 + 40 * 3_600_000)
        self.assertEqual(acc.hist, {})

    def test_preload_creator_history_from_observe_and_fast_creates(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            boot = 1_790_000_000_000
            day = time.strftime("%Y-%m-%d", time.gmtime(boot / 1000 - 3600))
            hour = time.strftime("%Y-%m-%dT%H", time.gmtime(boot / 1000 - 7200))
            obs = {"stream": "subscribeNewToken", "txType": "create", "mint": "A", "traderPublicKey": "CR", "t_ws": boot / 1000 - 1800}
            future = dict(obs, mint="F", t_ws=boot / 1000 + 5)
            (d / f"observe-{day}.jsonl").write_text(json.dumps(obs) + "\n" + json.dumps(future) + "\n", encoding="utf-8")
            fast = {"type": "create", "mint": "B", "creator": "CR", "block_time": int(boot / 1000 - 7000)}
            (d / f"creates-{hour}.jsonl").write_text(json.dumps(fast) + "\n", encoding="utf-8")
            acc = Exp012Online()
            self.assertEqual(acc.preload(d, boot), 2)
            self.assertEqual(len(acc.hist["CR"]), 2)
            self.assertNotIn("F", acc.hist_mints)  # rows at or after boot arrive live, not from disk


if __name__ == "__main__":
    unittest.main()
