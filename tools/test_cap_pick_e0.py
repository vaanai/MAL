"""Fixtures only: no /data/mal read. A tiny LightGBM model is trained here (as test_cap_pick_gate_replay does). git work happens in throwaway repos."""

from __future__ import annotations

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


def make_repo(root: Path, on_origin: bool = True) -> Path:
    """A throwaway git repo holding copies of the pinned files; `origin/main` points at HEAD when `on_origin`."""
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for rel in (*e0.PINNED_MODULES, e0.FROZEN_MD5_PATH):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, repo / rel)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pins")
    if on_origin:
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


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
        """What the real scorer does with a JSONL pick file: --book all = every decided mint (minus `drop`); --book picks = the `pick` ones."""
        def run(book: str, picks: Path, odir: Path):
            dec = {}
            for ln in picks.read_text(encoding="utf-8").splitlines():
                r = json.loads(ln)
                if r.get("kind") == "decision":
                    dec[r["mint"]] = r["decision"]
            universe = [m for m in sorted(dec) if m != drop]
            return (universe if book == "all" else [m for m in universe if dec[m] == "pick"]), ["fake", book]
        return run

    def run_e0(self, out: str = "out", **kw):
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
        lines, dead = e0.canon_from_b_records(recs)
        self.assertEqual(lines, ["B\tpick\t2\t0.9", "C\tno_features\t3\t"])
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
        self.assertTrue(res["equal_AB"], (out / "diff.tsv").read_text() if (out / "diff.tsv").exists() else "")
        self.assertFalse((out / "diff.tsv").exists())
        self.assertEqual(res["md5_A"], res["md5_B"])
        self.assertEqual((out / "A.canon").read_bytes(), (out / "B.canon").read_bytes())
        dec = {ln.split("\t")[0]: ln.split("\t")[1] for ln in (out / "A.canon").read_text().splitlines()}
        self.assertEqual(dec, {"Hi": "pick", "Lo": "below", "Mid": "pick", "HiH": "below", "Hi2": "pick"})  # HiH is below only because of the preloaded history
        self.assertEqual((res["n_A"], res["n_B"], res["n_picks"], res["n_picks_A"], res["n_dead_B"]), (5, 5, 3, 3, 1))
        self.assertEqual(res["A"]["history_rows"], 6)
        self.assertEqual(res["A"]["history_rows"], res["B"]["boots"][0]["history_rows"])
        self.assertEqual((res["A"]["rows_no_create"], res["A"]["staged_files"]), (res["A"]["rows_no_create"], 1))
        self.assertGreaterEqual(res["A"]["rows_no_create"], 3)  # NOCREATE and the two OLD rows are not fed
        self.assertTrue(res["equal_C"])
        self.assertEqual((out / "C.list").read_text(), "Hi\nMid\n")
        self.assertEqual((out / "Bpicks_in_U.list").read_text(), "Hi\nMid\n")
        self.assertEqual((res["C"]["n_Bpicks_not_in_U"], res["C"]["n_universe"]), (1, 4))
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
        rows, _st = e0.run_side_a(self.block, files, [str(self.view)], DAY, specs, self.dir, boot=False)
        self.assertEqual({r["mint"]: e0.label_from_gate_row(r) for r in rows}["HiH"], "pick")

    def test_forced_mismatch_writes_diff_and_exits_1(self) -> None:
        calls = []

        def factory():  # first call builds B's engine, the second yields A's book: a different threshold makes the sides disagree
            calls.append(1)
            return cp.build_engine(self.model, self.md5, self.feats, THR if len(calls) == 1 else 0.999, kill_dir=self.dir)

        res = e0.run_e0("fix", DAY, self.dir / "bad", block=self.block, engine_factory=factory, repo=self.repo, scorer=self.fake_scorer(), log=open("/dev/null", "w"))
        out = self.dir / "bad"
        self.assertFalse(res["equal_AB"])
        self.assertFalse(res["ok"])
        self.assertNotEqual(res["md5_A"], res["md5_B"])
        diff = (out / "diff.tsv").read_text().splitlines()
        self.assertEqual(diff[0], "mint\tA_line\tB_line")
        self.assertEqual(len(diff) - 1, res["n_diff"])
        self.assertTrue(any(ln.startswith("Hi\t") and "|pick|" in ln.split("\t")[2] and "|below|" in ln.split("\t")[1] for ln in diff[1:]), diff)
        self.assertTrue(res["equal_C"])  # C is judged against B, not A

    def test_main_exit_codes(self) -> None:
        patches = [mock.patch.object(cp, "BLOCKS", {"fix": self.block}), mock.patch.object(e0, "REPO", self.repo),
                   mock.patch.object(e0, "default_engine_factory", self.factory()),
                   mock.patch.object(e0, "subprocess_scorer", lambda *a, **k: self.fake_scorer()(a[6], a[4], a[5]))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "ok")]), 0)
        self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "ok")]), 2)  # not empty
        self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "skip"), "--skip-c"]), 1)  # no C: not ok
        self.assertEqual(e0.main(["check", str(self.dir / "ok" / "e0.json"), "--worktree", str(self.repo)]), 0)


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
            e0.run_e0("fix", "2026-8-17", self.dir / "x", block=self.block, repo=self.repo)
        with self.assertRaises(cp.Refused):  # sealed / void hours are refused by the replay's own rule
            (self.view / "VIEW.sha256").write_text("x", encoding="utf-8")
            e0.run_e0("fix", "2026-10-03", self.dir / "y", block=self.block, repo=self.repo)

    def test_main_exit_2_on_refusal(self) -> None:
        (self.repo / "scratch.txt").write_text("x", encoding="utf-8")
        with mock.patch.object(cp, "BLOCKS", {"fix": self.block}), mock.patch.object(e0, "REPO", self.repo):
            self.assertEqual(e0.main(["run", "--view", "fix", "--day", DAY, "--out", str(self.dir / "o")]), 2)


class PinTests(_Fix):
    def _e0(self) -> dict:
        pins = e0.collect_pins(self.repo)
        return {"blobs": pins["blobs"], "frozen_md5": pins["frozen_md5"], "commit": _git(self.repo, "rev-parse", "HEAD"), "ok": True}

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

    def test_check_refuses_when_the_recorded_e0_failed(self) -> None:
        rec = {**self._e0(), "ok": False}
        res = e0.verify_pins(rec, self.repo)
        self.assertTrue(all(m["ok"] for m in res["modules"].values()))
        self.assertFalse(res["ok"])

    def test_imported_modules_hash_to_the_head_blobs(self) -> None:
        imp = e0.imported_blobs(self.repo)
        pins = e0.collect_pins(self.repo)
        self.assertEqual({k: v["blob"] for k, v in imp.items()}, pins["blobs"])


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
        cmd = e0.scorer_cmd("explore-0814", roots7, "2026-08-17", Path("/o/B.jsonl"), Path("/o/s"), "picks", ["--k-mode", "hour"])
        self.assertEqual(cmd[1:3], ["-m", "tools.cap_pick_score"])
        self.assertEqual(cmd[cmd.index("--only-day") + 1], "2026-08-17")
        self.assertEqual((cmd[cmd.index("--book") + 1], cmd[cmd.index("--picks") + 1]), ("picks", "/o/B.jsonl"))
        self.assertEqual(cmd[-2:], ["--k-mode", "hour"])
        self.assertNotIn("--default-roots", cmd)

    def test_subprocess_scorer_reads_rows_csv_in_a_scorer_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td) / "tree"
            (tree / "tools").mkdir(parents=True)
            (tree / "tools" / "__init__.py").write_text("", encoding="utf-8")
            (tree / "tools" / "cap_pick_score.py").write_text(
                "import argparse, csv, pathlib\n"
                "ap = argparse.ArgumentParser(); ap.add_argument('--out-dir'); ap.add_argument('--book'); ap.add_argument('--picks'); ap.add_argument('--only-day')\n"
                "ap.add_argument('--p2-view-dir', action='append')\n"
                "a = ap.parse_args(); o = pathlib.Path(a.out_dir); o.mkdir(parents=True)\n"
                "w = csv.writer(open(o / 'rows.csv', 'w', newline='')); w.writerow(['day', 'mint'])\n"
                "[w.writerow([a.only_day, m]) for m in (['M1', 'M2'] if a.book == 'all' else ['M2'])]\n", encoding="utf-8")
            mints, cmd = e0.subprocess_scorer(tree, "explore-0814", ["/r/w1"], "2026-08-17", Path(td) / "B.jsonl", Path(td) / "s_all", "all", [], Path(td) / "all.log")
            self.assertEqual(mints, ["M1", "M2"])
            mints, _ = e0.subprocess_scorer(tree, "explore-0814", ["/r/w1"], "2026-08-17", Path(td) / "B.jsonl", Path(td) / "s_picks", "picks", [], Path(td) / "picks.log")
            self.assertEqual(mints, ["M2"])
            with self.assertRaises(e0.E0Error):  # an exit status other than 0 is a refusal
                e0.subprocess_scorer(tree, "explore-0814", ["/r/w1"], "2026-08-17", Path(td) / "B.jsonl", Path(td) / "s_picks", "picks", [], Path(td) / "again.log")


if __name__ == "__main__":
    unittest.main()
