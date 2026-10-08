"""Fixtures only: no /data/mal read. A tiny LightGBM model is trained here (as test_cap_pick_gate_replay does). git work happens in throwaway repos."""

from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import cap_pick_e0 as e0
from tools import cap_pick_gate_replay as cp
from tools.forward_paper import replay_rows
from tools.test_forward_exp012_gate import NAMES, _gated, _mint_rows
from tools.test_forward_paper import _trade

REPO = Path(__file__).resolve().parent.parent
DAY0 = 1_700_000_000_250 // 86_400_000 * 86_400_000  # 00:00Z of the fixture day
HOUR = 3_600_000
DAY = cp._day_of_ms(DAY0)
THR = 0.5


def _dump(row: dict) -> str:
    return json.dumps(row, separators=(",", ":"))


def cp_B0() -> int:
    from tools.test_forward_paper import B0

    return B0 // 2


def _crow(mint: str, t_ms: int, creator: str = "CA") -> dict:
    return {"type": "create", "mint": mint, "creator": creator, "block_time": t_ms // 1000, "event_ts": t_ms // 1000, "signature": "sig-" + mint,
            "quote_reserve": 35_000_000_000, "base_reserve": 1_073_000_000_000_000, "quote_mint": "So11111111111111111111111111111111111111112", "t_recv_ms": t_ms}


def _prior_model(dirpath: Path) -> tuple[Path, str, Path]:
    """Score is high iff n_buys >= 4 AND creator_prior_mints_24h < 3, so creator history decides a pick."""
    import lightgbm as lgb
    import numpy as np

    rng = np.random.RandomState(1)
    x = rng.rand(600, len(NAMES))
    x[:, NAMES.index("n_buys")] = rng.randint(0, 10, size=600)
    x[:, NAMES.index("creator_prior_mints_24h")] = rng.randint(0, 8, size=600)
    y = ((x[:, NAMES.index("n_buys")] >= 4) & (x[:, NAMES.index("creator_prior_mints_24h")] < 3)).astype(int)
    ds = lgb.Dataset(x, label=y, feature_name=NAMES, free_raw_data=False)
    booster = lgb.train({"objective": "binary", "verbose": -1, "num_threads": 1, "min_data_in_leaf": 5, "seed": 1, "deterministic": True}, ds, num_boost_round=30)
    model = dirpath / "model.txt"
    booster.save_model(str(model))
    feats = dirpath / "features.json"
    feats.write_text(json.dumps({"frozen_feature_names": NAMES}), encoding="utf-8")
    return model, hashlib.md5(model.read_bytes()).hexdigest(), feats


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


_TEMPLATE: list[Path] = []


def _template() -> Path:
    """One git repo holding a copy of tools/ and FROZEN.md5, built once per process: every imported tools.* module has a blob at HEAD in it."""
    if not _TEMPLATE:
        td = Path(tempfile.mkdtemp(prefix="e0_tpl_"))
        atexit.register(shutil.rmtree, td, True)
        repo = td / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        shutil.copytree(REPO / "tools", repo / "tools", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (repo / e0.FROZEN_MD5_PATH).parent.mkdir(parents=True)
        shutil.copyfile(REPO / e0.FROZEN_MD5_PATH, repo / e0.FROZEN_MD5_PATH)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "pins")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _TEMPLATE.append(repo)
    return _TEMPLATE[0]


def make_repo(root: Path, on_origin: bool = True) -> Path:
    """A throwaway copy of the template repo; `origin/main` points at HEAD when `on_origin`."""
    repo = root / "repo"
    shutil.copytree(_template(), repo, symlinks=True)
    if not on_origin:
        _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    return repo


FAKE_SCORER = """import argparse, csv, json, pathlib
from tools import helper_mod
ap = argparse.ArgumentParser()
for f in ("--out-dir", "--book", "--picks", "--only-day", "--exp022-source"):
    ap.add_argument(f)
ap.add_argument("--exp022", action="store_true")
ap.add_argument("--p2-view-dir", action="append")
a = ap.parse_args()
assert a.exp022 and a.exp022_source == "exploration" and a.book == "picks"
dec = {}
for ln in open(a.picks):
    r = json.loads(ln)
    if r.get("kind") == "decision":
        dec[r["mint"]] = r["decision"]
picks = sorted(m for m, d in dec.items() if d == "pick")
excluded = sorted(dec)[:1]
universe = sorted(dec)[1:]
attempts = [m for m in picks if m in universe]
lost = [m for m in picks if m not in universe]
o = pathlib.Path(a.out_dir)
o.mkdir(parents=True)
w = csv.writer(open(o / "rows.csv", "w", newline=""))
w.writerow(["day", "mint", "pnl_flat"])
[w.writerow([a.only_day, m, "SECRET_PNL"]) for m in attempts]
u = csv.writer(open(o / "universe.csv", "w", newline=""))
u.writerow(["mint", "status", "reason", "pick", "v0_missing"])
[u.writerow([m, "attempt", "", int(dec[m] == "pick"), 0]) for m in universe]
[u.writerow([m, "excluded", "mayhem", int(dec[m] == "pick"), 0]) for m in excluded]
summary = {"mode": "exp022", "exp022": {"source": a.exp022_source, "adapter": "fake", "constants": {"entry_latency_ms": 1300}, "flags_pinned": {"book": "picks"},
           "cells": {"primary_1300": {"book": "SECRET_PNL"}},
           "universe": {"attempts_in_universe": len(universe), "pick_attempts": len(attempts), "excluded_by_reason": {"mayhem": len(excluded)},
                        "report_only": {"tape_coverage_short": []}, "missing_v0": {"n_missing_v0": 0, "mints": [], "in_top3": {"x": True}}}},
           "picks_in_input": len(picks), "picks_not_attempts": {"mayhem": lost} if lost else {}, "picks": {"sha256": "x"}, "vmap": {"sha256": "y"},
           "sources": {}, "days": [a.only_day], "counts": {"hours": 24, "attempts": len(attempts), "pick_attempts": len(attempts), "fills": 7, "guarded": 1},
           "books": {"picks": "SECRET_PNL"}, "fail_legs": "SECRET_PNL"}
(o / "summary.json").write_text(json.dumps(summary))
"""


def stub_summary(n_picks: int, lost: dict | None = None) -> dict:
    """What subprocess_scorer returns under "summary" (already the filtered record)."""
    return {"mode": "exp022", "source": "exploration", "adapter": "fake", "constants": {"entry_latency_ms": 1300}, "flags_pinned": {"book": "picks"},
            "universe": {"attempts_in_universe": 0, "pick_attempts": 0, "excluded_by_reason": {}, "tape_coverage_short": 0, "n_missing_v0": 0},
            "picks_in_input": n_picks, "picks_not_attempts": lost or {}, "picks_sha256": "x", "vmap": {}, "sources": {}, "days": [],
            "counts": {"hours": 24, "attempts": 0, "pick_attempts": 0}}


def make_scorer_tree(root: Path) -> Path:
    """A clean git tree on origin holding a fake scorer that imports one helper module."""
    tree = root / "scorer_tree"
    (tree / "tools").mkdir(parents=True)
    (tree / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (tree / "tools" / "helper_mod.py").write_text("X = 1\n", encoding="utf-8")
    (tree / "tools" / "cap_pick_score.py").write_text(FAKE_SCORER, encoding="utf-8")
    _git(tree, "init", "-q", "-b", "main")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "scorer")
    _git(tree, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tree


def good_record(repo: Path, **over) -> dict:
    """An e0.json record that `check` accepts against `repo`: the real pins and imported modules, equal md5s, no dry run, the pinned scope."""
    pins = e0.collect_pins(repo)
    mods = e0.collect_imported_modules(repo, e0.loaded_tools_modules())
    rec = {"schema": e0.SCHEMA, "view": e0.E0_VIEW, "day": e0.E0_DAY, "dry_run": False, "e0_criterion": e0.E0_CRITERION, "ok": True,
           "blobs": pins["blobs"], "frozen_md5": pins["frozen_md5"], "commit": _git(repo, "rev-parse", "HEAD"),
           "md5_A_decide": "aa", "md5_B_decide": "aa", "n_create_ms_disagree": 0, "md5_C": "cc", "md5_Bpicks_U": "cc", "n_C": 5,
           "scorer_flags": list(e0.SCORER_FLAGS), "scorer_summary": {"mode": "exp022", "source": "exploration"},
           "checks": {k: True for k in e0.SANITY_KEYS}, "imported_module_mismatches": [], "imported_module_blobs": mods["blobs"]}
    rec.update(over)
    return rec


class _Fix(unittest.TestCase):
    """A one-root view with a day of creates and trades (plain .jsonl), a creator with history, a mint created before the restart."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.model, self.md5, self.feats = _prior_model(self.dir)
        self.repo = make_repo(self.dir)
        self.view = self.dir / "view" / "w1"
        for k in ("creates", "trades"):
            (self.view / k).mkdir(parents=True)
        (self.view / "VIEW.sha256").write_text("deadbeef  ./creates/x\n", encoding="utf-8")
        self.block = cp.Block("fix", (str(self.view),), ".jsonl")
        self.build()

    def put(self, kind: str, hour: str, rows: list[dict]) -> None:
        p = self.view / kind / f"{kind}-{hour}.jsonl"
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(old + "".join(_dump(r) + "\n" for r in rows), encoding="utf-8")

    def build(self) -> None:
        t_prev = DAY0 - 2 * HOUR  # CH made 5 mints two hours before the restart
        self.put("creates", cp._hour_of_ms(t_prev), [_crow(f"H{i}", t_prev + i * 1000, "CH") for i in range(5)] + [_crow("OLD", t_prev + 9000, "CZ")])
        t3, t4 = DAY0 + 3 * HOUR, DAY0 + 4 * HOUR
        h3, h4 = cp._hour_of_ms(t3), cp._hour_of_ms(t4)
        spec = {"Hi": ("C1", 7), "Lo": ("C2", 1), "Mid": ("C3", 5), "HiH": ("CH", 7)}  # HiH: strong flow but a creator with 5 prior mints
        self.put("creates", h3, [_crow(m, t3, c) for m, (c, _n) in spec.items()])
        trades = [r for m, (_c, n) in spec.items() for r in _mint_rows(m, n, t0=t3)]
        trades.append(dict(_trade("OLD", t3 + 1_000, trader="w"), tx_index=1))  # the pre-restart mint: a dead row in B
        trades.append(dict(_trade("OLD", t3 + 30_000, venue="pumpswap", slot=100, quote=70_000_000_000, base=cp_B0()), tx_index=2))
        trades.append(dict(_trade("NOCREATE", t3 + 2_000, trader="w"), tx_index=3))  # never created: not fed to A
        self.put("trades", h3, sorted(trades, key=lambda r: r["t_recv_ms"]))
        self.put("creates", h4, [_crow("Hi2", t4, "C9")])
        self.put("trades", h4, sorted(_mint_rows("Hi2", 6, t0=t4), key=lambda r: r["t_recv_ms"]))

    def factory(self):
        return lambda: cp.build_engine(self.model, self.md5, self.feats, THR, kill_dir=self.dir)

    @staticmethod
    def fake_scorer(drop: str | None = None):
        """What the real scorer's EXP-022 mode does with a JSONL pick file: U = every decided mint but `drop`; C = the picks inside U."""
        def run(picks: Path, odir: Path):
            dec = {}
            for ln in picks.read_text(encoding="utf-8").splitlines():
                r = json.loads(ln)
                if r.get("kind") == "decision":
                    dec[r["mint"]] = r["decision"]
            universe = [m for m in sorted(dec) if m != drop]
            n_picks = sum(1 for d in dec.values() if d == "pick")
            lost = {"mayhem": [drop]} if drop and dec.get(drop) == "pick" else {}
            return {"attempts": [m for m in universe if dec[m] == "pick"], "universe": universe, "excluded": {"mayhem": 1} if drop else {},
                    "summary": stub_summary(n_picks, lost), "cmd": ["fake", "--exp022"]}
        return run

    def run_e0(self, out: str = "out", **kw):
        kw.setdefault("dry_run", True)
        return e0.run_e0("fix", DAY, self.dir / out, block=self.block, engine_factory=self.factory(), repo=self.repo, log=open("/dev/null", "w"), **kw)


class CanonTests(unittest.TestCase):
    def test_sort_empty_score_repr_and_duplicates(self) -> None:
        rows = [("Zed", "pick", 5000, 0.1 + 0.2), ("Abc", "no_features", 7000, None), ("Mid", "below", 6000, 1.0), ("Aaa", "below", 1, 5e-324)]
        lines = e0.canon_lines(rows)
        self.assertEqual(lines, ["Aaa\tbelow\t1\t5e-324", "Abc\tno_features\t7000\t", "Mid\tbelow\t6000\t1.0", "Zed\tpick\t5000\t0.30000000000000004"])
        self.assertEqual(e0.canon_text(lines), "".join(x + "\n" for x in lines))
        self.assertEqual(e0.canon_text([]), "")
        with self.assertRaises(e0.E0Error):
            e0.canon_lines([("A", "pick", 1, 0.9), ("A", "below", 2, 0.1)], "B")

    def test_dead_rows_and_meta_are_dropped(self) -> None:
        recs = [{"kind": "meta"}, {"kind": "decision", "mint": "B", "decision": "pick", "mig_ms": 2, "score": 0.9},
                {"kind": "dead", "mint": "A", "decision": "pre_restart", "first_pumpswap_ms": 1},
                {"kind": "decision", "mint": "C", "decision": "no_features", "mig_ms": 3, "score": None}]
        rows, dead = e0.b_side_rows(recs)
        self.assertEqual(e0._lines(rows, "B"), ["B\tpick\t2\t0.9", "C\tno_features\t3\t"])
        self.assertEqual([r[:4] for r in rows], [("B", "pick", 2, 0.9), ("C", "no_features", 3, None)])
        self.assertEqual(dead, 1)

    def test_label_mapping_is_one_to_one(self) -> None:
        f = e0.label_from_gate_row
        self.assertEqual(f({"entered": True, "reason": None}), "pick")
        self.assertEqual(f({"entered": False, "reason": "below_threshold"}), "below")
        for reason in ("no_features", "no_bond_history", "gate_error"):
            self.assertEqual(f({"entered": False, "reason": reason}), reason)
        self.assertEqual(f({"entered": False, "reason": None}), "unknown")

    def test_diff_rows(self) -> None:
        a = ["A\tpick\t1\t0.9", "B\tbelow\t2\t0.1", "C\tpick\t3\t0.8"]
        b = ["A\tpick\t1\t0.9", "B\tbelow\t2\t0.2", "D\tpick\t4\t0.8"]
        self.assertEqual(e0.diff_rows(a, b), [("B", "B|below|2|0.1", "B|below|2|0.2"), ("C", "C|pick|3|0.8", "<absent>"), ("D", "<absent>", "D|pick|4|0.8")])
        self.assertEqual(e0.diff_rows(a, a), [])


class HookTests(_Fix):
    def test_default_none_is_unchanged_and_boot_loads_history(self) -> None:
        creates = [cp.create_signal_from_row(_crow("HiH", DAY0 + 3 * HOUR, "CH")), cp.create_signal_from_row(_crow("Hi", DAY0 + 3 * HOUR, "C1"))]
        rows = sorted((r for m in ("HiH", "Hi") for r in _mint_rows(m, 7, t0=DAY0 + 3 * HOUR)), key=lambda r: r["t_recv_ms"])
        spec = [r.spec for r in self.factory()().books]
        kw = dict(tape_end_ms=DAY0 + 5 * HOUR, kill_file=self.dir / "KILL", offsets_ms=(5_000,))
        plain = replay_rows(creates, rows, spec, **kw)
        none = replay_rows(creates, rows, spec, exp012_boot=None, **kw)
        self.assertEqual(plain.exp012_rows, none.exp012_rows)
        stage = self.dir / "stage"
        stage.mkdir()
        files = cp.hour_files(self.block, [str(self.view)])
        self.assertEqual(cp.stage_creates(files, DAY0, stage, [str(self.view)]), 1)
        booted = replay_rows(creates, rows, spec, exp012_boot=(stage, DAY0), **kw)
        prior = {r["mint"]: r["features"]["creator_prior_mints_24h"] for r in booted.exp012_rows}
        self.assertEqual(prior, {"HiH": 5.0, "Hi": 0.0})
        self.assertEqual({r["mint"]: r["features"]["creator_prior_mints_24h"] for r in plain.exp012_rows}, {"HiH": 0.0, "Hi": 0.0})
        self.assertEqual({r["mint"]: r["entered"] for r in booted.exp012_rows}, {"HiH": False, "Hi": True})
        self.assertEqual({r["mint"]: r["entered"] for r in plain.exp012_rows}, {"HiH": True, "Hi": True})


class RunTests(_Fix):
    def test_a_equals_b_with_boot_history_and_c_equals_b_picks_in_universe(self) -> None:
        res = self.run_e0(scorer=self.fake_scorer(drop="Hi2"))
        out = self.dir / "out"
        self.assertTrue(res["equal_full"] and res["equal_decide"], (out / "diff.tsv").read_text() if (out / "diff.tsv").exists() else "")
        self.assertFalse((out / "diff.tsv").exists())
        self.assertFalse((out / "diff_gt60.tsv").exists())  # no mint is older than 60 min here
        self.assertEqual(res["e0_criterion"], "le60_either_plus_B_picks")
        self.assertEqual(res["md5_A_full"], res["md5_B_full"])
        self.assertEqual(res["md5_A_decide"], res["md5_B_decide"])
        self.assertEqual((out / "A.canon").read_bytes(), (out / "B.canon").read_bytes())
        self.assertEqual((out / "A.decide.canon").read_bytes(), (out / "A.canon").read_bytes())
        self.assertEqual((out / "A.decide.md5").read_text(), f"{res['md5_A_decide']}  A.decide.canon\n")
        dec = {ln.split("\t")[0]: ln.split("\t")[1] for ln in (out / "A.canon").read_text().splitlines()}
        self.assertEqual(dec, {"Hi": "pick", "Lo": "below", "Mid": "pick", "HiH": "below", "Hi2": "pick"})  # HiH is below only because of the preloaded history
        self.assertEqual((res["n_A_full"], res["n_B_full"], res["n_A_decide"], res["n_B_decide"], res["n_gt60"], res["n_picks"], res["n_picks_A"], res["n_dead_B"]), (5, 5, 5, 5, 0, 3, 3, 1))
        self.assertEqual(res["A"]["history_rows"], 6)
        self.assertEqual(res["A"]["history_rows"], res["B"]["boots"][0]["history_rows"])
        self.assertEqual((res["A"]["rows_no_create"], res["A"]["staged_files"]), (res["A"]["rows_no_create"], 1))
        self.assertGreaterEqual(res["A"]["rows_no_create"], 3)  # NOCREATE and the two OLD rows are not fed
        self.assertTrue(res["equal_C"])
        self.assertEqual((out / "C.list").read_text(), "Hi\nMid\n")
        self.assertEqual((out / "Bpicks_in_U.list").read_text(), "Hi\nMid\n")
        self.assertEqual((res["C"]["n_Bpicks_not_in_U"], res["C"]["n_universe"]), (1, 4))
        self.assertEqual(res["Bpicks_not_in_U"], ["Hi2"])  # the mints, not only the count; report only
        self.assertEqual(res["picks_not_attempts_by_reason"], {"mayhem": 1})
        self.assertEqual((res["n_universe"], res["scorer_flags"]), (4, ["--exp022", "--exp022-source", "exploration", "--book", "picks"]))
        self.assertEqual((res["scorer_summary"]["mode"], res["scorer_summary"]["source"]), ("exp022", "exploration"))
        self.assertEqual((out / "Bpicks_not_in_U.list").read_text(), "Hi2\n")
        self.assertEqual(json.loads((out / "e0.json").read_text())["Bpicks_not_in_U"], ["Hi2"])
        self.assertEqual(res["md5_C"], hashlib.md5(b"Hi\nMid\n").hexdigest())
        self.assertTrue(res["ok"], res["checks"])
        self.assertEqual(json.loads((out / "e0.json").read_text())["commit"], _git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(set(res["blobs"]), set(e0.PINNED_MODULES))
        self.assertEqual(len(res["view_sha256"]), 1)
        self.assertEqual(res["view_sha256"][str(self.view)], hashlib.sha256((self.view / "VIEW.sha256").read_bytes()).hexdigest())
        # B.jsonl is the replay CLI's layout: the compare loader reads it
        on, dead, meta = cp.load_online([out / "B.jsonl"])
        self.assertEqual((sorted(on), sorted(dead)), (sorted(dec), ["OLD"]))
        self.assertEqual(meta["fix"]["days"], [DAY])

    def test_without_the_boot_hook_a_differs_from_b(self) -> None:
        """The reason the hook exists: without preload A has no creator history and calls HiH a pick."""
        files = cp.hour_files(self.block, [str(self.view)])
        specs = [r.spec for r in self.factory()().books]
        rows, _st, _cr = e0.run_side_a(self.block, files, [str(self.view)], DAY, specs, self.dir, boot=False)
        self.assertEqual({r["mint"]: e0.label_from_gate_row(r) for r in rows}["HiH"], "pick")

    def test_forced_mismatch_writes_diff_and_exits_1(self) -> None:
        calls = []

        def factory():  # first call builds B's engine, the second yields A's book: a different threshold makes the sides disagree
            calls.append(1)
            return cp.build_engine(self.model, self.md5, self.feats, THR if len(calls) == 1 else 0.999, kill_dir=self.dir)

        res = e0.run_e0("fix", DAY, self.dir / "bad", block=self.block, engine_factory=factory, repo=self.repo, scorer=self.fake_scorer(), log=open("/dev/null", "w"),
                        dry_run=True)
        out = self.dir / "bad"
        self.assertFalse(res["equal_full"])
        self.assertFalse(res["equal_decide"])
        self.assertFalse(res["ok"])
        self.assertNotEqual(res["md5_A_decide"], res["md5_B_decide"])
        diff = (out / "diff.tsv").read_text().splitlines()
        self.assertEqual(diff[0], "mint\tA_line\tB_line")
        self.assertEqual(len(diff) - 1, res["n_diff"])
        self.assertTrue(any(ln.startswith("Hi\t") and "|pick|" in ln.split("\t")[2] and "|below|" in ln.split("\t")[1] for ln in diff[1:]), diff)
        self.assertTrue(res["equal_C"])  # C is judged against B, not A

    def test_main_exit_codes(self) -> None:
        patches = [mock.patch.object(cp, "BLOCKS", {"fix": self.block}), mock.patch.object(e0, "REPO", self.repo),
                   mock.patch.object(e0, "default_engine_factory", self.factory()),
                   mock.patch.object(e0, "subprocess_scorer", lambda *a, **k: self.fake_scorer()(a[4], a[5]))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "ok"), "--dry-run"]), 0)
        self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "ok"), "--dry-run"]), 2)  # not empty
        self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "skip"), "--skip-c", "--dry-run"]), 1)  # no C: not ok
        self.assertEqual(e0.main(["check", str(self.dir / "ok" / "e0.json"), "--worktree", str(self.repo)]), 2)  # a dry-run file is not the E0 record


class CompareTests(unittest.TestCase):
    LIMIT = 3_600_000

    def test_decide_set_edge_crosstab_and_gt60_lines(self) -> None:
        a = [("E", "pick", 5_000 + self.LIMIT, 0.9, 5_000), ("L", "below", 6_001 + self.LIMIT, 0.2, 6_000), ("S", "pick", 100, 0.9, 0), ("G", "pick", 9 + self.LIMIT, 0.8, 8)]
        b = [("E", "pick", 5_000 + self.LIMIT, 0.9, 5_000), ("L", "no_features", 6_001 + self.LIMIT, None, 6_000), ("S", "pick", 100, 0.9, 0), ("G", "no_features", 9 + self.LIMIT, None, 8)]
        r = e0.compare_sides(a, b)
        self.assertEqual(r["a_decide"], ["E\tpick\t3605000\t0.9", "S\tpick\t100\t0.9"])  # exactly 60 min is kept, 1 ms over is not
        self.assertEqual(r["a_decide"], r["b_decide"])
        self.assertEqual((r["diff_decide"], r["n_decide_set"]), ([], 2))
        self.assertEqual(r["crosstab_gt60"], {"below": {"no_features": 1}, "pick": {"no_features": 1}})
        self.assertEqual([x[0] for x in r["gt60"]], ["G", "L"])
        self.assertEqual(r["gt60"][1][1:3], ("L|below|3606001|0.2", "L|no_features|3606001|"))
        self.assertEqual([x[5] for x in r["gt60"]], ["no", "no"])
        self.assertNotEqual(r["a_full"], r["b_full"])

    def test_a_only_score_at_61_min_with_b_no_features_gives_equal(self) -> None:
        old = 61 * 60_000
        r = e0.compare_sides([("M", "pick", old, 0.9, 0), ("K", "below", 1000, 0.1, 0)], [("M", "no_features", old, None, 0), ("K", "below", 1000, 0.1, 0)])
        self.assertEqual(r["a_decide"], r["b_decide"])
        self.assertEqual(r["a_decide"], ["K\tbelow\t1000\t0.1"])
        self.assertNotEqual(r["a_full"], r["b_full"])
        self.assertEqual(r["crosstab_gt60"], {"pick": {"no_features": 1}})

    def test_b_pick_at_61_min_that_a_scores_below_gives_not_equal(self) -> None:
        old = 61 * 60_000
        r = e0.compare_sides([("M", "below", old, 0.2, 0), ("K", "below", 1000, 0.1, 0)], [("M", "pick", old, 0.9, 0), ("K", "below", 1000, 0.1, 0)])
        self.assertNotEqual(r["a_decide"], r["b_decide"])
        self.assertEqual(r["diff_decide"], [("M", "M|below|3660000|0.2", "M|pick|3660000|0.9")])
        self.assertEqual([x[5] for x in r["gt60"]], ["yes"])
        # the same pick, decided the same way by A, is equal
        r2 = e0.compare_sides([("M", "pick", old, 0.9, 0)], [("M", "pick", old, 0.9, 0)])
        self.assertEqual((r2["a_decide"], r2["diff_decide"]), (r2["b_decide"], []))
        self.assertEqual(len(r2["a_decide"]), 1)
        # a B pick that A never decided is a missing line on A
        r3 = e0.compare_sides([], [("M", "pick", old, 0.9, 0)])
        self.assertEqual(r3["diff_decide"], [("M", "<absent>", "M|pick|3660000|0.9")])

    def test_a_pick_at_61_min_that_b_does_not_pick_is_outside_the_set(self) -> None:
        r = e0.compare_sides([("M", "pick", 61 * 60_000, 0.9, 0)], [("M", "no_features", 61 * 60_000, None, 0)])
        self.assertEqual((r["a_decide"], r["b_decide"], r["n_decide_set"]), ([], [], 0))

    def test_le60_on_either_side_puts_the_mint_in_the_set(self) -> None:
        # the sides disagree about the create time: within 60 min for A, 61 min for B. Both are in the set, the create-time mismatch is reported
        r = e0.compare_sides([("M", "below", 3_000_000, 0.2, 0)], [("M", "no_features", 3_000_000, None, -700_000)])
        self.assertEqual(r["n_decide_set"], 1)
        self.assertNotEqual(r["a_decide"], r["b_decide"])
        self.assertEqual(r["create_disagree"], [("M", 0, -700_000)])

    def test_create_time_disagreement_in_the_set_is_a_mismatch(self) -> None:
        a = [("M", "pick", 100, 0.9, 0)]
        b = [("M", "pick", 100, 0.9, 1_000)]  # same line, different create time
        r = e0.compare_sides(a, b)
        self.assertEqual(r["a_decide"], r["b_decide"])
        self.assertEqual(r["create_disagree"], [("M", 0, 1_000)])
        # outside the set (older than 60 min on both sides, not a B pick) a create-time difference is not reported as a decision mismatch
        r = e0.compare_sides([("N", "below", 4_000_000, 0.2, 0)], [("N", "no_features", 4_000_000, None, 1_000)])
        self.assertEqual(r["create_disagree"], [])

    def test_a_create_time_comes_from_the_gate_row_else_the_raw_signal_time(self) -> None:
        rows = [{"mint": "F", "entered": True, "reason": None, "mig_ms": 7_000_000, "score": 0.9, "features": {"time_to_migrate_s": 1000.0}},
                {"mint": "N", "entered": False, "reason": "no_features", "mig_ms": 9_000_000, "score": None, "features": None}]
        out = e0.a_side_rows(rows, {"F": 5_999_500, "N": 8_000_999})
        self.assertEqual([(r[0], r[4]) for r in out], [("F", 6_000_000), ("N", 8_000_999)])
        with self.assertRaises(e0.E0Error):
            e0.a_side_rows(rows, {"F": 1})

    def test_no_feature_row_with_a_subsecond_create_time_is_equal_when_both_sides_use_the_raw_value(self) -> None:
        sig = 8_000_999
        a_rows = e0.a_side_rows([{"mint": "N", "entered": False, "reason": "no_bond_history", "mig_ms": 9_000_000, "score": None, "features": None}], {"N": sig})
        b_rows, _dead = e0.b_side_rows([{"kind": "decision", "mint": "N", "decision": "no_bond_history", "mig_ms": 9_000_000, "score": None, "create_ms": sig}])
        r = e0.compare_sides(a_rows, b_rows)
        self.assertEqual(r["n_decide_set"], 1)  # 999,001 ms old: inside 60 min
        self.assertEqual(r["a_decide"], ["N\tno_bond_history\t9000000\t"])
        self.assertEqual((r["a_decide"], r["diff_decide"], r["create_disagree"]), (r["b_decide"], [], []))

    def test_mint_on_one_side_only_and_missing_create_time(self) -> None:
        r = e0.compare_sides([("X", "pick", self.LIMIT + 10, 0.9, 0)], [])
        self.assertEqual(r["crosstab_gt60"], {"pick": {"<absent>": 1}})
        with self.assertRaises(e0.E0Error):
            e0.compare_sides([], [("Y", "pick", 1, 0.9, None)])


class LateMigratorTests(_Fix):
    """A mint that migrates 61.7 min after its create: A (no prune at second steps / 50,000 prints) scores it, B (prune per 5,000 prints) drops it."""

    def build(self) -> None:
        super().build()
        t3, t4 = DAY0 + 3 * HOUR, DAY0 + 4 * HOUR
        h3, h4 = cp._hour_of_ms(t3), cp._hour_of_ms(t4)
        self.put("creates", h3, [_crow("SLOW", t3, "C5"), _crow("TICK", t3, "C6")])
        slow = _mint_rows("SLOW", 7, t0=t3, mig_after_ms=3_700_000)
        self.put("trades", h3, [r for r in slow if r["t_recv_ms"] < t4])
        tick_t0 = t3 + 3_630_000  # 5,001 bonding prints of TICK, 6 ms apart, drive B's print counter past 5,000 at about 60.99 min
        tick = [dict(_trade("TICK", tick_t0 + 6 * i, trader=f"w{i % 50}", slot=300 + i // 5, event_index=1 + i % 5), tx_index=i % 7) for i in range(5_001)]
        self.put("trades", h4, sorted([r for r in slow if r["t_recv_ms"] >= t4] + tick, key=lambda r: r["t_recv_ms"]))

    def test_a_only_score_at_61_min_with_b_no_features_is_equal(self) -> None:
        res = self.run_e0(scorer=self.fake_scorer())
        out = self.dir / "out"
        self.assertFalse(res["equal_full"])
        self.assertTrue(res["equal_decide"])
        self.assertNotEqual(res["md5_A_full"], res["md5_B_full"])
        self.assertEqual(res["md5_A_decide"], res["md5_B_decide"])
        self.assertEqual((res["n_A_full"], res["n_B_full"], res["n_A_decide"], res["n_B_decide"], res["n_gt60"]), (6, 6, 5, 5, 1))
        self.assertEqual(res["crosstab_gt60"], {"pick": {"no_features": 1}})
        self.assertEqual(res["n_create_ms_disagree"], 0)
        self.assertTrue(res["equal_C"])
        self.assertTrue(res["ok"], res["checks"])
        gt = (out / "diff_gt60.tsv").read_text().splitlines()
        self.assertEqual(len(gt), 2)
        self.assertTrue(gt[1].startswith("SLOW\tSLOW|pick|") and "\tSLOW|no_features|" in gt[1], gt)
        self.assertFalse((out / "diff.tsv").exists())  # diff.tsv holds the criterion's differences only
        self.assertNotIn("SLOW", (out / "B.decide.canon").read_text())
        self.assertIn("SLOW\tpick", (out / "A.canon").read_text())
        self.assertNotIn("SLOW", (out / "A.decide.canon").read_text())

    def test_main_exits_0_on_the_deciding_set_criterion(self) -> None:
        patches = [mock.patch.object(cp, "BLOCKS", {"fix": self.block}), mock.patch.object(e0, "REPO", self.repo),
                   mock.patch.object(e0, "default_engine_factory", self.factory()),
                   mock.patch.object(e0, "subprocess_scorer", lambda *a, **k: self.fake_scorer()(a[4], a[5]))]
        for p_ in patches:
            p_.start()
            self.addCleanup(p_.stop)
        self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "late"), "--dry-run"]), 0)
        self.assertEqual(json.loads((self.dir / "late" / "e0.json").read_text())["e0_criterion"], "le60_either_plus_B_picks")


class RefusalTests(_Fix):
    def test_dirty_tree_refuses(self) -> None:
        (self.repo / "tools" / "forward_paper.py").write_text("# edited\n", encoding="utf-8")
        with self.assertRaises(e0.E0Error) as cm:
            self.run_e0(scorer=self.fake_scorer())
        self.assertIn("not clean", str(cm.exception))
        self.assertFalse((self.dir / "out").exists())

    def test_untracked_file_refuses(self) -> None:
        (self.repo / "scratch.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(e0.E0Error):
            self.run_e0(scorer=self.fake_scorer())

    def test_head_not_on_origin_refuses(self) -> None:
        (self.repo / "tools" / "cap_pick_gate_replay.py").write_text("# local only\n", encoding="utf-8")
        _git(self.repo, "commit", "-q", "-am", "unpushed")
        with self.assertRaises(e0.E0Error) as cm:
            self.run_e0(scorer=self.fake_scorer())
        self.assertIn("not on any origin", str(cm.exception))

    def test_missing_view_hash_and_bad_day_refuse(self) -> None:
        (self.view / "VIEW.sha256").unlink()
        with self.assertRaises(e0.E0Error):
            self.run_e0(scorer=self.fake_scorer())
        with self.assertRaises(e0.E0Error):
            e0.run_e0("fix", "2026-8-17", self.dir / "x", block=self.block, repo=self.repo, dry_run=True)
        with self.assertRaises(cp.Refused):  # sealed / void hours are refused by the replay's own rule
            (self.view / "VIEW.sha256").write_text("x", encoding="utf-8")
            e0.run_e0("fix", "2026-10-03", self.dir / "y", block=self.block, repo=self.repo, dry_run=True)

    def test_main_exit_2_on_refusal(self) -> None:
        (self.repo / "scratch.txt").write_text("x", encoding="utf-8")
        with mock.patch.object(cp, "BLOCKS", {"fix": self.block}), mock.patch.object(e0, "REPO", self.repo):
            self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "o"), "--dry-run"]), 2)


class PinTests(_Fix):
    def _e0(self) -> dict:
        return good_record(self.repo)

    def test_blobs_match_git_and_frozen_md5_is_the_pinned_value(self) -> None:
        pins = e0.collect_pins(self.repo)
        for rel in e0.PINNED_MODULES:
            self.assertEqual(pins["blobs"][rel], _git(self.repo, "rev-parse", f"HEAD:{rel}"))
            self.assertEqual(pins["blobs"][rel], _git(REPO, "hash-object", str(REPO / rel)))  # the throwaway copy is the real file
        self.assertEqual(pins["frozen_md5"], {"path": e0.FROZEN_MD5_PATH, "md5": "a01f05dfb1e622f78b2bba55d174be09", "expected": "a01f05dfb1e622f78b2bba55d174be09", "ok": True})
        self.assertTrue(e0.verify_pins(self._e0(), self.repo)["ok"])

    def test_check_catches_head_change_working_edit_and_frozen_change(self) -> None:
        rec = self._e0()
        p = self.repo / "tools" / "exploration_entry_model.py"
        p.write_text(p.read_text() + "\n# edit\n", encoding="utf-8")  # working copy only
        res = e0.verify_pins(rec, self.repo)
        self.assertFalse(res["ok"])
        m = res["modules"]["tools/exploration_entry_model.py"]
        self.assertEqual((m["recorded"] == m["head"], m["ok"]), (True, False))
        _git(self.repo, "commit", "-q", "-am", "edit")  # now HEAD differs too
        res = e0.verify_pins(rec, self.repo)
        self.assertFalse(res["modules"]["tools/exploration_entry_model.py"]["ok"])
        self.assertNotEqual(res["modules"]["tools/exploration_entry_model.py"]["head"], rec["blobs"]["tools/exploration_entry_model.py"])
        self.assertTrue(all(res["modules"][r]["ok"] for r in e0.PINNED_MODULES if r != "tools/exploration_entry_model.py"))
        # FROZEN.md5 changes
        rec2 = self._e0()
        f = self.repo / e0.FROZEN_MD5_PATH
        f.write_text(f.read_text() + "x\n", encoding="utf-8")
        _git(self.repo, "commit", "-q", "-am", "frozen")
        self.assertFalse(e0.verify_pins(rec2, self.repo)["frozen_md5"]["ok"])

    def test_check_recomputes_ok_and_ignores_the_stored_one(self) -> None:
        good = e0.verify_pins(good_record(self.repo), self.repo)
        self.assertTrue(good["ok"], good["recomputed"])
        bad_fields = {
            "equal_decide": {"md5_B_decide": "bb"},
            "create_ms_disagreement": {"n_create_ms_disagree": 1},
            "equal_C": {"md5_Bpicks_U": "dd"},
            "n_C_positive": {"n_C": 0},
            "scorer_mode": {"scorer_summary": {"mode": "exp022", "source": "walk2"}},
            "sanity": {"checks": {"boot_history_equal": True, "A_log_rows_equal_engine_rows": True, "nonempty": False}},
            "scope": {"day": "2026-08-17"},
            "criterion": {"e0_criterion": "le60"},
            "no_import_mismatch": {"imported_module_mismatches": [{"module": "tools.laya_v0"}]},
            "not_a_dry_run": {"dry_run": True},
        }
        for part, over in bad_fields.items():
            res = e0.verify_pins(good_record(self.repo, ok=True, **over), self.repo)  # the stored ok says True
            self.assertFalse(res["ok"], part)
            self.assertFalse(res["recomputed"]["parts"][part if part != "create_ms_disagreement" else "equal_decide"], part)
        self.assertTrue(res["stored_ok"])
        # a stored False does not matter either: the fields decide
        res = e0.verify_pins(good_record(self.repo, ok=False), self.repo)
        self.assertTrue(res["ok"])
        self.assertIs(res["stored_ok"], False)
        self.assertFalse(e0.verify_pins({k: v for k, v in good_record(self.repo).items() if k != "dry_run"}, self.repo)["ok"])  # an old record has no dry_run

    def test_check_refuses_a_dry_run_record(self) -> None:
        rec = good_record(self.repo, dry_run=True)
        self.assertFalse(e0.verify_pins(rec, self.repo)["ok"])
        path = self.dir / "dry.json"
        path.write_text(json.dumps(rec), encoding="utf-8")
        self.assertEqual(e0.main(["check", str(path), "--worktree", str(self.repo)]), 2)
        path.write_text(json.dumps(good_record(self.repo)), encoding="utf-8")
        self.assertEqual(e0.main(["check", str(path), "--worktree", str(self.repo)]), 0)

    def test_imported_modules_hash_to_the_head_blobs(self) -> None:
        imp = e0.imported_blobs(self.repo)
        pins = e0.collect_pins(self.repo)
        self.assertEqual({k: v["blob"] for k, v in imp.items()}, pins["blobs"])


class ScopeTests(_Fix):
    def pinned(self):
        for p in (mock.patch.object(e0, "E0_VIEW", "fix"), mock.patch.object(e0, "E0_DAY", DAY)):
            p.start()
            self.addCleanup(p.stop)

    def test_other_view_or_day_needs_dry_run(self) -> None:
        for view, day in (("explore-0814", "2026-08-17"), ("fix", DAY), ("exp011-0909", "2026-08-20")):
            with self.assertRaises(e0.E0Error) as cm:
                e0.run_e0(view, day, self.dir / "x", block=self.block, repo=self.repo, scorer=self.fake_scorer())
            self.assertIn("--dry-run", str(cm.exception))
            self.assertFalse((self.dir / "x").exists())
        self.assertEqual(e0.E0_VIEW, "explore-0814")
        self.assertEqual(e0.E0_DAY, "2026-08-20")
        with mock.patch.object(cp, "BLOCKS", {"fix": self.block}):
            self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "y")]), 2)

    def test_pinned_scope_runs_without_dry_run_and_check_accepts_the_record(self) -> None:
        self.pinned()
        res = e0.run_e0("fix", DAY, self.dir / "rec", block=self.block, engine_factory=self.factory(), repo=self.repo, scorer=self.fake_scorer(),
                        log=open("/dev/null", "w"))
        self.assertIs(res["dry_run"], False)
        self.assertEqual(res["e0_pin"], {"view": "fix", "day": DAY})
        self.assertTrue(res["ok"], res["checks"])
        path = self.dir / "rec" / "e0.json"
        self.assertEqual(e0.main(["check", str(path), "--worktree", str(self.repo)]), 0)
        # a hand-edited record with a stored ok is still recomputed
        doc = json.loads(path.read_text())
        doc["md5_B_decide"] = "tampered"
        path.write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(e0.main(["check", str(path), "--worktree", str(self.repo)]), 1)

    def test_empty_scorer_universe_is_not_ok_in_run_or_check(self) -> None:
        """Empty C and empty B picks in U have equal md5s; that is not an equality."""
        self.pinned()
        empty = lambda picks, odir: {"attempts": [], "universe": [], "excluded": {}, "summary": stub_summary(3), "cmd": ["fake"]}  # noqa: E731
        res = e0.run_e0("fix", DAY, self.dir / "emp", block=self.block, engine_factory=self.factory(), repo=self.repo, scorer=empty, log=open("/dev/null", "w"))
        self.assertTrue(res["equal_C"])
        self.assertEqual(res["md5_C"], hashlib.md5(b"").hexdigest())
        self.assertEqual(res["n_C"], 0)
        self.assertTrue(res["equal_decide"])
        self.assertFalse(res["checks"]["nonempty"])
        self.assertFalse(res["ok"])
        path = self.dir / "emp" / "e0.json"
        doc = json.loads(path.read_text())
        self.assertFalse(e0.recompute_ok(doc)["parts"]["n_C_positive"])
        self.assertEqual(e0.main(["check", str(path), "--worktree", str(self.repo)]), 1)
        forged = {**doc, "ok": True, "checks": {**doc["checks"], "nonempty": True}}  # a doctored record: stored ok and the sanity flag say True
        self.assertFalse(e0.recompute_ok(forged)["ok"])
        path.write_text(json.dumps(forged), encoding="utf-8")
        self.assertEqual(e0.main(["check", str(path), "--worktree", str(self.repo)]), 1)
        self.assertFalse(e0.recompute_ok({k: v for k, v in doc.items() if k != "n_C"})["parts"]["n_C_positive"])  # no n_C recorded: not ok

    def test_scorer_arg_needs_dry_run(self) -> None:
        self.pinned()
        with self.assertRaises(e0.E0Error) as cm:
            e0.run_e0("fix", DAY, self.dir / "z", block=self.block, engine_factory=self.factory(), repo=self.repo, scorer=self.fake_scorer(), scorer_extra=["--k-mode", "hour"])
        self.assertIn("--scorer-arg", str(cm.exception))
        self.assertFalse((self.dir / "z").exists())
        with mock.patch.object(cp, "BLOCKS", {"fix": self.block}):
            self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "z2"), "--scorer-arg=--k-mode", "--scorer-arg=hour"]), 2)
        res = self.run_e0("dz", scorer=self.fake_scorer(), scorer_extra=["--k-mode", "hour"])  # a dry run passes the guard
        self.assertIs(res["dry_run"], True)

    def test_scorer_repo_needs_dry_run(self) -> None:
        self.pinned()
        tree = make_scorer_tree(self.dir)
        with self.assertRaises(e0.E0Error) as cm:
            e0.run_e0("fix", DAY, self.dir / "w", block=self.block, engine_factory=self.factory(), repo=self.repo, scorer_repo=tree)
        self.assertIn("--scorer-repo", str(cm.exception))
        self.assertFalse((self.dir / "w").exists())
        with mock.patch.object(cp, "BLOCKS", {"fix": self.block}):
            self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "w2"), "--scorer-repo", str(tree)]), 2)

    def test_non_dry_run_needs_the_scorer_at_head(self) -> None:
        self.pinned()
        # our own scorer-less repo: the template carries the real scorer now, so remove it and commit
        _git(self.repo, "rm", "-q", "tools/cap_pick_score.py")
        _git(self.repo, "commit", "-q", "-m", "no scorer")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        with self.assertRaises(e0.E0Error) as cm:
            e0.run_e0("fix", DAY, self.dir / "n", block=self.block, engine_factory=self.factory(), repo=self.repo)
        self.assertIn("tools/cap_pick_score.py must exist at HEAD", str(cm.exception))
        self.assertFalse((self.dir / "n").exists())
        # with the scorer committed at HEAD, a non-dry run uses it from this same tree
        (self.repo / "tools" / "cap_pick_score.py").write_text(FAKE_SCORER, encoding="utf-8")
        (self.repo / "tools" / "helper_mod.py").write_text("X = 1\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "scorer at HEAD")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        with mock.patch.object(e0, "E0_VIEW", "explore-0814"):  # the real scorer command needs a view it knows
            res = e0.run_e0("explore-0814", DAY, self.dir / "n2", block=self.block, engine_factory=self.factory(), repo=self.repo, log=open("/dev/null", "w"))
        self.assertIs(res["dry_run"], False)
        self.assertTrue(res["equal_C"], res["C"])
        self.assertEqual(res["C"]["scorer_head"], _git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(res["scorer_imported_module_blobs"], {})
        self.assertEqual(res["imported_module_blobs"]["tools.cap_pick_score"], _git(self.repo, "rev-parse", "HEAD:tools/cap_pick_score.py"))
        self.assertIn("scorer", res["imported_module_sides"]["tools.helper_mod"])
        self.assertTrue(res["ok"], res["checks"])

    def test_scorer_args_cannot_override_the_exp022_constants_even_in_a_dry_run(self) -> None:
        for bad in (["--exp022-source=walk2"], ["--exp022-source", "walk2"], ["--book=all"], ["--book", "all"], ["--exp"], ["--exp022"], ["--picks=/x"], ["--only-day=2026-08-20"],
                    ["--out-dir=/x"], ["--b"], ["--x", "--exp022-s=walk2"]):
            with self.assertRaises(e0.E0Error, msg=bad):
                e0.check_scorer_extra(bad)
            with self.assertRaises(e0.E0Error, msg=bad):
                self.run_e0("zz", scorer=self.fake_scorer(), scorer_extra=bad)  # dry_run=True is the helper's default
        self.assertFalse((self.dir / "zz").exists())
        e0.check_scorer_extra(["--hour-sph-json=/x", "hour", "--k-mode"])  # other flags pass (in a dry run)

    def test_a_dry_run_is_marked_and_check_refuses_it(self) -> None:
        res = self.run_e0(scorer=self.fake_scorer())
        self.assertIs(res["dry_run"], True)
        self.assertTrue(json.loads((self.dir / "out" / "e0.json").read_text())["dry_run"])
        self.assertEqual(e0.main(["check", str(self.dir / "out" / "e0.json"), "--worktree", str(self.repo)]), 2)


class ImportedModuleTests(_Fix):
    def test_run_records_every_imported_tools_module_against_head(self) -> None:
        res = self.run_e0(scorer=self.fake_scorer())
        blobs = res["imported_module_blobs"]
        for name in ("tools.forward_paper", "tools.forward_exp012_gate", "tools.exploration_entry_model", "tools.cap_pick_gate_replay", "tools.paper_price_path",
                     "tools.laya_v0", "tools.cap_pick_e0"):
            self.assertIn(name, blobs)
            self.assertEqual(blobs[name], _git(self.repo, "rev-parse", f"HEAD:{name.replace('.', '/')}.py"), name)
        self.assertGreater(res["n_imported_modules"], 10)
        self.assertEqual(res["imported_module_mismatches"], [])
        self.assertTrue(res["checks"]["imported_modules"])
        self.assertEqual(res["imported_module_sides"]["tools.laya_v0"], ["A_B"])
        self.assertEqual(res["scorer_imported_module_blobs"], {})
        self.assertEqual(json.loads((self.dir / "out" / "e0.json").read_text())["imported_module_blobs"], blobs)
        self.assertTrue(res["ok"], res["checks"])

    def test_an_imported_module_that_differs_from_head_fails_ok(self) -> None:
        """The loaded laya_v0.py is the real one; the tree's HEAD holds an edited laya_v0.py, committed, clean and on origin."""
        f = self.repo / "tools" / "laya_v0.py"
        f.write_text(f.read_text(encoding="utf-8") + "\n# a different file at HEAD\n", encoding="utf-8")
        _git(self.repo, "commit", "-q", "-am", "edit laya_v0")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        res = self.run_e0(scorer=self.fake_scorer())
        self.assertTrue(res["equal_decide"] and res["equal_C"])
        self.assertFalse(res["checks"]["imported_modules"])
        self.assertFalse(res["ok"])
        self.assertEqual([m["module"] for m in res["imported_module_mismatches"]], ["tools.laya_v0"])
        m = res["imported_module_mismatches"][0]
        self.assertNotEqual(m["head_blob"], m["file_blob"])

    def test_scorer_modules_come_from_importtime_and_are_recorded_apart_for_another_tree(self) -> None:
        tree = make_scorer_tree(self.dir)
        res = e0.run_e0("explore-0814", DAY, self.dir / "sc", block=self.block, engine_factory=self.factory(), repo=self.repo, scorer_repo=tree,
                        log=open("/dev/null", "w"), dry_run=True)
        self.assertTrue(res["equal_C"], res["C"])
        self.assertEqual(sorted(res["scorer_imported_module_blobs"]), ["tools.cap_pick_score", "tools.helper_mod"])
        for name in ("tools.helper_mod", "tools.cap_pick_score"):
            self.assertEqual(res["scorer_imported_module_blobs"][name], _git(tree, "rev-parse", f"HEAD:{name.replace('.', '/')}.py"))
            self.assertIn("scorer", res["imported_module_sides"][name])
        self.assertNotIn("tools.helper_mod", res["imported_module_blobs"])
        self.assertEqual(res["imported_module_mismatches"], [])
        self.assertTrue(res["ok"], res["checks"])
        # check: the scorer-only modules need the scorer tree
        self.assertFalse(e0.verify_pins(res, self.repo)["imported_modules"]["ok"])
        ok = e0.verify_pins(res, self.repo, tree)
        self.assertTrue(ok["imported_modules"]["ok"], ok["imported_modules"])
        # everything else recomputes true; this record is a dry run on a non-pinned day, so only those two parts fail
        self.assertEqual([k for k, v in ok["recomputed"]["parts"].items() if not v], ["scope", "not_a_dry_run"])
        self.assertFalse(ok["ok"])

    def test_same_tree_scorer_modules_merge_into_the_flat_dict(self) -> None:
        r = e0.collect_imported_modules(self.repo, {}, {"tools.laya_v0", "tools.nope"}, self.repo)
        self.assertEqual(sorted(r["blobs"]), ["tools.laya_v0", "tools.nope"])
        self.assertEqual(r["scorer_blobs"], {})
        self.assertEqual([m["module"] for m in r["mismatches"]], ["tools.nope"])  # absent at HEAD
        self.assertEqual(r["blobs"]["tools.nope"], None)

    def test_importtime_parser(self) -> None:
        text = ("import time: self [us] | cumulative | imported package\n"
                "import time:       120 |        120 |   _io\n"
                "import time:       300 |       4500 | tools\n"
                "import time:       210 |       2100 |     tools.paper_price_path\n"
                "import time:        55 |         55 |   tools.laya_v0\n"
                "import time:        10 |         10 |   mytools.nope\n"
                "scorer said import time: nothing\n")
        self.assertEqual(e0.modules_from_importtime(text), {"tools.paper_price_path", "tools.laya_v0"})

    def test_check_verifies_all_recorded_modules_not_only_the_pinned_four(self) -> None:
        rec = self._rec()
        self.assertTrue(e0.verify_pins(rec, self.repo)["ok"])
        f = self.repo / "tools" / "paper_price_path.py"  # not one of the four pins
        f.write_text(f.read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8")
        _git(self.repo, "commit", "-q", "-am", "edit paper_price_path")
        res = e0.verify_pins(rec, self.repo)
        self.assertTrue(all(m["ok"] for m in res["modules"].values()))  # the four pins still match
        self.assertFalse(res["imported_modules"]["ok"])
        self.assertEqual([b["module"] for b in res["imported_modules"]["bad"]], ["tools.paper_price_path"])
        self.assertFalse(res["ok"])
        # an e0.json that recorded no modules cannot be checked
        no_mods = {k: v for k, v in rec.items() if k != "imported_module_blobs"}
        self.assertFalse(e0.verify_pins(no_mods, self.repo)["imported_modules"]["ok"])

    def _rec(self) -> dict:
        return good_record(self.repo)


class ScorerTests(unittest.TestCase):
    def test_source_flags_per_view(self) -> None:
        roots7 = [f"/data/mal/clean-view/explore-0814/w{i}" for i in range(1, 8)]
        flags = e0.scorer_source_flags("explore-0814", roots7)
        self.assertEqual(flags, [x for r in roots7 for x in ("--p2-view-dir", r)])
        self.assertEqual(e0.scorer_source_flags("fresh-0903", [f"/data/mal/blocks-clean/fresh-0903/w{i}" for i in (1, 2, 3)]), ["--p3-root", "/data/mal/blocks-clean/fresh-0903"])
        self.assertEqual(e0.scorer_source_flags("fast-pool-0918", ["/d/fast"]), ["--p1-fast-dir", "/d/fast"])
        for view, block in cp.BLOCKS.items():
            self.assertIn(view, e0.SCORER_SOURCE)
            e0.scorer_source_flags(view, block.roots)
        cmd = e0.scorer_cmd("explore-0814", roots7, "2026-08-17", Path("/o/B.jsonl"), Path("/o/s"), ["--k-mode", "hour"])
        self.assertEqual(cmd[1:5], ["-X", "importtime", "-m", "tools.cap_pick_score"])
        self.assertEqual(cmd[cmd.index("--only-day") + 1], "2026-08-17")
        self.assertEqual(cmd[cmd.index("--picks") + 1], "/o/B.jsonl")
        i = cmd.index("--exp022")  # the mode is a constant: --exp022 --exp022-source exploration --book picks, in this order
        self.assertEqual(cmd[i:i + 5], ["--exp022", "--exp022-source", "exploration", "--book", "picks"])
        self.assertEqual(e0.SCORER_FLAGS, ("--exp022", "--exp022-source", "exploration", "--book", "picks"))
        self.assertNotIn("all", cmd)
        self.assertEqual(cmd[-2:], ["--k-mode", "hour"])
        self.assertNotIn("--default-roots", cmd)

    def test_subprocess_scorer_reads_only_mints_and_filtered_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = make_scorer_tree(Path(td))
            b = Path(td) / "B.jsonl"
            b.write_text("".join(json.dumps(r) + "\n" for r in (
                {"schema": "x", "kind": "meta"}, {"kind": "decision", "mint": "M1", "decision": "pick"}, {"kind": "decision", "mint": "M2", "decision": "pick"},
                {"kind": "decision", "mint": "M3", "decision": "below"})), encoding="utf-8")
            res = e0.subprocess_scorer(tree, "explore-0814", ["/r/w1"], "2026-08-17", b, Path(td) / "s", [], Path(td) / "s.log")
            self.assertEqual(res["attempts"], ["M2"])  # M1 sorts first and the fake scorer excludes it
            self.assertEqual(res["universe"], ["M2", "M3"])
            self.assertEqual(res["excluded"], {"mayhem": 1})
            self.assertEqual(res["cmd"][res["cmd"].index("--exp022"):][:5], list(e0.SCORER_FLAGS))
            sm = res["summary"]
            self.assertEqual((sm["mode"], sm["source"], sm["constants"], sm["picks_in_input"], sm["picks_not_attempts"]), ("exp022", "exploration", {"entry_latency_ms": 1300}, 2, {"mayhem": ["M1"]}))
            self.assertEqual(sm["universe"]["excluded_by_reason"], {"mayhem": 1})
            self.assertEqual(sm["counts"]["attempts"], 1)
            self.assertNotIn("fills", sm["counts"])
            self.assertTrue({"cells", "books", "fail_legs", "exit_types", "config"}.isdisjoint(sm))
            self.assertNotIn("SECRET", json.dumps(res))
            with self.assertRaises(e0.E0Error):  # an exit status other than 0 is a refusal
                e0.subprocess_scorer(tree, "explore-0814", ["/r/w1"], "2026-08-17", b, Path(td) / "s", [], Path(td) / "again.log")

    def test_summary_record_refuses_other_modes_and_sources(self) -> None:
        ok = {"mode": "exp022", "exp022": {"source": "exploration", "constants": {"a": 1}, "universe": {}}, "counts": {"fills": 3, "hours": 24}}
        self.assertEqual(e0.scorer_summary_record(ok)["counts"]["hours"], 24)
        self.assertNotIn("fills", e0.scorer_summary_record(ok)["counts"])
        for bad in ({**ok, "mode": "phase2"}, {**ok, "exp022": {**ok["exp022"], "source": "walk2"}}, {"mode": "exp022"}):
            with self.assertRaises(e0.E0Error):
                e0.scorer_summary_record(bad)


class Exp022ModeTests(_Fix):
    def test_c_is_rows_u_is_universe_csv_and_no_pnl_reaches_e0_json(self) -> None:
        tree = make_scorer_tree(self.dir)
        res = e0.run_e0("explore-0814", DAY, self.dir / "x22", block=self.block, engine_factory=self.factory(), repo=self.repo, scorer_repo=tree,
                        log=open("/dev/null", "w"), dry_run=True)
        out = self.dir / "x22"
        # the fake scorer's universe drops the alphabetically first mint ("Hi", a B pick): U = 4 attempts, C = the other two picks
        self.assertEqual((res["n_universe"], res["n_C"], res["C"]["n_attempt_rows"]), (4, 2, 2))
        self.assertEqual((out / "C.list").read_text(), "Hi2\nMid\n")
        self.assertEqual((out / "Bpicks_in_U.list").read_text(), "Hi2\nMid\n")
        self.assertEqual(res["Bpicks_not_in_U"], ["Hi"])
        self.assertEqual(res["picks_not_attempts_by_reason"], {"mayhem": 1})
        self.assertEqual(res["C"]["excluded_by_reason"], {"mayhem": 1})
        self.assertTrue(res["equal_C"] and res["checks"]["scorer_picks_in_input"])
        self.assertTrue(res["ok"], res["checks"])
        sm = res["scorer_summary"]
        self.assertEqual((sm["mode"], sm["source"], sm["picks_in_input"]), ("exp022", "exploration", 3))
        self.assertEqual(res["scorer_flags"], ["--exp022", "--exp022-source", "exploration", "--book", "picks"])
        text = (out / "e0.json").read_text()
        self.assertNotIn("SECRET", text)  # nothing of the scorer's P&L was read or copied
        self.assertNotIn('"fills"', text)
        self.assertEqual(sum(1 for c in res["C"]["cmd"] if c == "--book"), 1)

    def test_a_scorer_that_read_a_different_pick_count_fails_the_sanity_check(self) -> None:
        def short(picks, odir):
            r = self.fake_scorer()(picks, odir)
            r["summary"] = stub_summary(1)  # the scorer says it read 1 pick, B decided 3
            return r
        res = self.run_e0(scorer=short)
        self.assertFalse(res["checks"]["scorer_picks_in_input"])
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
