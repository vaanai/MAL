"""osb_proof: PASS / FAIL / VACUOUS and every pre-check refusal, on the synthetic fixtures of tools/test_osb_follow.py (no real tape, ledger, registry or key is opened).
The leg runner is in-process (a test-only hook; the CLI exposes none); the production runner is a subprocess of osb_follow.py. Nothing here says any book makes money."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "ops"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import osb_proof as P  # noqa: E402
import osb_follow as O  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402
from tools.arena_rules_testkit import hour_name  # noqa: E402
from tools.test_osb_follow import FAST, RT, START_HOUR, _env, make_world, manifest, shifted_rows  # noqa: E402,F401  (_env: the autouse fixture)

HOUR = 3_600_000
SEED_EXACT = O.hour_key(H.parse_hour(START_HOUR) - 201 * HOUR)
S_MS = H.parse_hour(START_HOUR)


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return tmp_path_factory.mktemp("osbproof")


class Env:
    def __init__(self, tmp):
        self.tmp = tmp
        _, src, names = make_world(tmp, shifted_rows(tmp))
        self.keep = tmp / "keep"
        self.keep.mkdir()
        for nm in names:
            shutil.copy(src / nm, self.keep / nm)
        for x in range(S_MS - 201 * HOUR, S_MS - 26 * HOUR, HOUR):  # seed hours before the fixture tape: empty files
            (self.keep / hour_name(x)).touch()
        self.n = 0

    def mf(self, **over):
        base = dict(tape_dir=str(self.keep), seed_from_hour=SEED_EXACT, out_root=str(self.tmp / "orig-out"), rule_overrides=FAST)
        base.update(over)
        mp, m = manifest(self.tmp, **base)
        return str(mp)

    def test_kw(self, **extra):
        def cli(mp, args, log):
            kw = {}
            if "--once" in args:
                kw["once"] = True
            if "--max-hours" in args:
                kw["max_hours"] = int(args[args.index("--max-hours") + 1])
            code = O.follow(mp, _roots=(str(self.tmp),), **RT, **kw)
            if hook := extra.get("after"):
                hook(mp, args, code)
            return code, 1234, 0.5
        t = {"roots": (str(self.tmp),), "skip_env": True, "git": lambda: (True, True, "a" * 40), "min_hour": "2000-01-01T00", "cli": cli, "table_mode": "first_print", "engine_kw": RT["_engine_kw"], "min_entries": 3}
        t.update({k: v for k, v in extra.items() if k != "after"})
        return t

    def proof_root(self):
        self.n += 1
        return str(self.tmp / "proof-2026-11-13" / ("r%d" % self.n))

    def run(self, mans, hours, **extra):
        return P.run_proof(mans, START_HOUR, hours, self.proof_root(), str(self.keep), _test=self.test_kw(**extra))


@pytest.fixture(scope="module")
def env(shared):
    return Env(shared)


@pytest.fixture(scope="module")
def pass_run(env):
    mans = [env.mf(), env.mf(rule_overrides={**FAST, "TP": 1.3})]
    code, rep, line = env.run(mans, 24)
    return code, rep, line, mans


def test_full_proof_passes_on_the_fixture(pass_run):
    code, rep, line, mans = pass_run
    assert code == 0 and line.startswith("OSB-PROOF PASS "), (line, rep and rep["fails"])
    assert rep["hours"] == 24 and rep["head"] == "a" * 40 and len(rep["manifests"]) == 2
    for r in rep["manifests"]:
        assert r["md5_by_hour_lines"] == {"L1": 24, "L3": 24}
        assert r["L1_digests"] == r["L2_digests"] == r["L3_digests"] and set(r["L1_digests"]) == {"decision_md5", "all_kinds_digest", "sequence_md5", "ledger_tree_md5"}
        assert r["legs"]["L3"]["exit_codes"] == [0, 0] and r["legs"]["L1"]["exit_codes"] == [0] and r["legs"]["L2"]["exit_codes"] == [0]
        assert r["row_counts_by_kind"].get("entry", 0) >= 3
        assert r["manifest_sha256"] and all(len(v) == 64 for v in r["derived_manifest_sha256"].values())
    assert rep["note"] == P.GUARD_LINE and rep["guard_path_exercised"] is False  # the plain fixture has no gap rows


def test_derived_manifests_change_only_out_root_and_tape_dir_and_the_window_is_hard_links(pass_run, env):
    code, rep, line, mans = pass_run
    root = sorted((env.tmp / "proof-2026-11-13").iterdir())[0]
    orig = json.loads(Path(mans[0]).read_text())
    der = json.loads((root / "M0" / "manifest-L1.json").read_text())
    assert {k for k in orig if orig[k] != der[k]} == {"out_root", "tape_dir"}
    names = sorted(p.name for p in (root / "M0" / "tape").iterdir())
    assert len(names) == 201 + 24 and all(os.stat(root / "M0" / "tape" / n).st_nlink >= 2 for n in names)  # linked from keep, never copied
    assert names[0] == hour_name(S_MS - 201 * HOUR) and names[-1] == hour_name(S_MS + 23 * HOUR)
    assert json.loads((root / "proof.json").read_text())["verdict"] == "PASS"


def test_output_carries_none_of_the_forbidden_fields(pass_run, env):
    code, rep, line, mans = pass_run
    root = sorted((env.tmp / "proof-2026-11-13").iterdir())[0]
    text = (root / "proof.json").read_text() + line + json.dumps(rep)
    for bad in ("pnl", "exit_reason", "tp_1", "time_12h", "time_24h", "\"pool\"", "mint", "price", "sol_in", "stake_sol", "\"reason\""):
        assert bad not in text.lower(), bad
    assert set(rep["manifests"][0]) >= {"legs", "row_counts_by_kind", "md5_by_hour_lines", "L1_digests"} and "_vac" not in rep["manifests"][0]
    for r in rep["manifests"]:
        for lg in r["legs"].values():
            assert set(lg) == {"exit_codes", "peak_rss_kib", "wall_s"}


def test_a_perturbed_leg_is_a_fail(env):
    def tamper(mp, args, code):
        if mp.endswith("manifest-L1.json"):
            f = Path(json.loads(Path(mp).read_text())["out_root"]) / "md5-by-hour.jsonl"
            lines = f.read_text().splitlines()
            d = json.loads(lines[1])
            d["ledger_md5"] = "0" * 32
            lines[1] = json.dumps(d)
            f.write_text("\n".join(lines) + "\n")
    code, rep, line = env.run([env.mf()], 4, after=tamper)
    assert code == 1 and line.startswith("OSB-PROOF FAIL ") and "md5-by-hour.jsonl L1 vs L3" in rep["fails"]


def test_a_non_zero_exit_code_is_a_fail(env):
    base = env.test_kw()["cli"]

    def cli(mp, args, log):
        code, rss, w = base(mp, args, log)
        return (4 if mp.endswith("manifest-L3.json") and "--once" in args else code), rss, w
    code, rep, line = env.run([env.mf()], 4, cli=cli)
    assert code == 1 and "exit code" in rep["fails"]


def test_a_window_without_entries_is_vacuous_not_a_pass(env):
    code, rep, line = env.run([env.mf()], 4, min_entries=5)
    assert code == 3 and line.startswith("OSB-PROOF VACUOUS ") and "n_entries<5" in line and rep["verdict"] == "VACUOUS"


@pytest.mark.parametrize("what", ["dirty", "branch", "env", "exists", "missing_hour", "start", "tape", "seed", "early", "root", "odd"])
def test_refusals_exit_2(env, what, monkeypatch):
    t = env.test_kw()
    kw = dict(mans=[env.mf()], hours=4, root=env.proof_root())
    if what == "dirty":
        t["git"] = lambda: (False, True, "b" * 40)
    elif what == "branch":
        t["git"] = lambda: (True, False, "b" * 40)
    elif what == "env":
        t["skip_env"] = False
        monkeypatch.setenv("MAL_ARENA_TEST", "1")
    elif what == "exists":
        Path(kw["root"]).mkdir(parents=True)
    elif what == "missing_hour":
        (env.keep / hour_name(S_MS + 3 * HOUR)).rename(env.tmp / "moved")
    elif what == "start":
        kw["mans"] = [env.mf(start_hour="2026-11-02T03")]
    elif what == "tape":
        kw["mans"] = [env.mf(tape_dir=str(env.tmp / "elsewhere"))]
    elif what == "seed":
        kw["mans"] = [env.mf(seed_from_hour=O.hour_key(S_MS - 202 * HOUR))]
    elif what == "early":
        t["min_hour"] = "2026-11-02T01"  # the seed hours of the fixture start 201 h before START_HOUR
    elif what == "root":
        kw["root"] = str(env.tmp / "not-a-proof-dir" / "r")
    elif what == "odd":
        kw["hours"] = 5
    try:
        code, rep, line = P.run_proof(kw["mans"], START_HOUR, kw["hours"], kw["root"], str(env.keep), _test=t)
    finally:
        if what == "missing_hour":
            (env.tmp / "moved").rename(env.keep / hour_name(S_MS + 3 * HOUR))
    assert code == 2 and rep is None and line.startswith("OSB-PROOF REFUSED"), line
    if what != "exists":
        assert not Path(kw["root"]).exists()


def test_cli_refuses_a_keep_outside_the_real_root_and_the_need_hours_follow_the_driver(env):
    assert P.main(["--manifest", env.mf(), "--start-hour", START_HOUR, "--hours", "4", "--proof-root", env.proof_root(), "--keep", str(env.keep)]) == 2
    from tools import arena_rule_l1 as L
    m = {"rule_overrides": {}}
    assert P.need_hours(m) == 201 and P.need_hours({"rule_overrides": {"AGE_HI": 300 * 3600}}) == 301 and L.AGE_HI == 200 * 3600
