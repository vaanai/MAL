"""tools/hv7_smoke.py tests. SYNTHETIC tape, fake caller (StubModel) only. No API call, no key value, no network, no /data/mal path, no HV4/HV6/Q50 output.
Nothing here says HV7 makes money."""
from __future__ import annotations

import json
import socket
import subprocess
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.haiku_caller as hcall
from tools import arena_paper as AP
from tools import hv4_cards as C
from tools import hv4_model as m4
from tools import hv6_agents as A
from tools import hv6_cards as H
from tools import hv6_runner as H6
from tools import hv7_register as REG
from tools import hv7_runner as RN
from tools import hv7_smoke as S
from tools.arena_state import ArenaState
from tools.haiku_caller import Ledger
from tools.test_hv4_runner import MIN, FakeChain, T0, write_tape
from tools.test_hv7_runner import POOLS, HOUR, leader_first, tape_rows

FAKE_KEY = "sk-ant-api03-TESTKEYTESTKEYTESTKEY"
SHA = "a" * 40
DAY = "2026-10-27"


@pytest.fixture(autouse=True)
def _env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(RN, "READ_ROOTS", (base,))
    monkeypatch.setattr(RN, "WRITE_ROOTS", (base,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)

    def no_api(*a, **k):
        raise AssertionError("HaikuCaller used in a test")

    monkeypatch.setattr(hcall.HaikuCaller, "__init__", no_api)
    monkeypatch.setattr(m4, "HaikuCaller", hcall.HaikuCaller)


def rules():
    return SimpleNamespace(AP=AP, C=C, M4=m4, A=A, H=H, H6=H6, REG=REG, RN=RN, ArenaState=ArenaState, repo=str(Path(__file__).resolve().parent.parent))


def args(smk, **kw):
    d = dict(smk=str(smk), cap_usd=0.10, periods=31, warmup_ms=8 * MIN, frozen_sha=SHA, feed_delay_slots=2)
    d.update(kw)
    return Namespace(**d)


def sim(tmp_path, *, cost=0.0, latency=50, script=leader_first, periods=5, **kw):
    tape = tmp_path / "tape"
    write_tape(tape, tape_rows())
    parent = tmp_path / "smoke"
    parent.mkdir()
    made = {}

    def mk(guard):
        made["m"] = A.StubModel(script, guard=guard, cost_usd=cost, latency_ms=latency)
        return made["m"]

    rep = S.run_smoke(rules(), args(parent / "s1", periods=periods, **kw), model=mk, chain=FakeChain(), tape_dir=str(tape), live=False, start_ms=T0 + HOUR, parent=str(parent),
                      verify_sha=False, engine_kw={"sps_fn": lambda: 0.4}, period_ms=4 * MIN, anchor_h02=False)      # unit-test grid only: fast, fake chain; the CLI cannot reach it
    return rep, parent / "s1", made["m"]


# --------------------------------------------------------------------------------------------------------------------- synthetic report roots
def put_rank_rows(root: Path, rows):
    d = root / "decisions" / "L"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{DAY}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def rank(i, **kw):
    r = {"kind": "rank", "model_error": None, "refused": False, "latency_ms": 1000 + i, "cost_usd": 0.001, "late": False, "ok": True, "fallback": False,
         "fallback_reason": None, "shown": ["SECRETPOOL"], "pick": "SECRETPOOL", "order": ["SECRETPOOL"]}
    r.update(kw)
    return r


def put_period_rows(root: Path, n=31, cards=8):
    d = root / "decisions" / "SCAN"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{DAY}.jsonl").write_text("".join(json.dumps({"kind": "period", "no_trade": False, "card_shas": ["x"] * cards, "vetoed": [], "screen": ["SECRETPOOL"]}) + "\n" for _ in range(n)))


def put_cost(root: Path, usd: float):
    p = root / "ledgers" / "RANK-L"
    p.mkdir(parents=True, exist_ok=True)
    led = Ledger(p / f"{DAY}.jsonl")
    with led.locked(exclusive=True):
        fd = led.open_append()
        try:
            Ledger.append_row(fd, {"cost_usd": usd, "ts_ms": 1, "source": "stub", "request_sha256": "r"})
        finally:
            import os

            os.close(fd)


WIN = (0, S.MIN_SPAN_MS + 45_000)


def base_root(tmp_path, n=31, **kw):
    root = tmp_path / "root"
    put_rank_rows(root, [rank(i) for i in range(n)])
    put_period_rows(root, n)
    return root


def test_verdict_pass(tmp_path):
    rep = S.report(base_root(tmp_path), counters={}, window_ms=WIN)
    assert rep["verdict"] == "PASS" and rep["calls"] == 31 and rep["valid_permutations"] == 31 and rep["error_free_calls"] == 31
    assert rep["call_latency_ms"]["p50"] is not None and rep["call_latency_ms"]["p95"] is not None


def test_verdict_incomplete_under_30_error_free(tmp_path):
    rep = S.report(base_root(tmp_path, n=29), counters={}, window_ms=WIN)
    assert rep["verdict"] == "INCOMPLETE" and "fewer_than_30_error_free_calls" in rep["incomplete_reasons"]
    root = tmp_path / "r2"        # 31 calls, 1 API error: 30 error-free, fallback 3.2%: PASS (the floor is 30, not 31)
    put_rank_rows(root, [rank(i, model_error=("APIError" if i < 1 else None), fallback=i < 1, ok=i >= 1) for i in range(31)])
    put_period_rows(root)
    one = S.report(root, counters={}, window_ms=WIN)
    assert one["verdict"] == "PASS" and one["error_free_calls"] == 30 and one["model_error_counts"] == {"APIError": 1}
    root3 = tmp_path / "r3"       # 30 calls, 1 error: 29 error-free and 3.3% fallback: INCOMPLETE
    put_rank_rows(root3, [rank(i, model_error=("APIError" if i < 1 else None), fallback=i < 1, ok=i >= 1) for i in range(30)])
    put_period_rows(root3, 30)
    assert S.report(root3, counters={}, window_ms=WIN)["verdict"] == "INCOMPLETE"


def test_verdict_incomplete_when_span_under_2h(tmp_path):
    rep = S.report(base_root(tmp_path), counters={}, window_ms=(0, S.MIN_SPAN_MS - 1000))
    assert rep["verdict"] == "INCOMPLETE" and "span_under_2h" in rep["incomplete_reasons"]


def test_verdict_fail_fallback_share(tmp_path):
    root = tmp_path / "r"
    put_rank_rows(root, [rank(i, fallback=i < 2, ok=i >= 2, fallback_reason=("not_a_permutation" if i < 2 else None)) for i in range(31)])      # 2/31 = 6.45%
    put_period_rows(root)
    rep = S.report(root, counters={}, window_ms=WIN)
    assert rep["verdict"] == "FAIL" and "fallback_share_above_5pct" in rep["fail_reasons"]
    root2 = tmp_path / "r2"
    put_rank_rows(root2, [rank(i, fallback=i < 1, ok=i >= 1) for i in range(31)])        # 1/31 = 3.2%
    put_period_rows(root2)
    assert "fallback_share_above_5pct" not in S.report(root2, counters={}, window_ms=WIN)["fail_reasons"]


def test_verdict_fail_latency_p95(tmp_path):
    root = tmp_path / "r"
    put_rank_rows(root, [rank(i, latency_ms=(9000 if i >= 10 else 500)) for i in range(31)])
    put_period_rows(root)
    rep = S.report(root, counters={}, window_ms=WIN)
    assert rep["verdict"] == "FAIL" and "call_latency_p95_above_8s" in rep["fail_reasons"]
    root2 = tmp_path / "r2"
    put_rank_rows(root2, [rank(i, latency_ms=8000) for i in range(31)])          # exactly 8 s is not above 8 s
    put_period_rows(root2)
    assert S.report(root2, counters={}, window_ms=WIN)["verdict"] == "PASS"


def test_verdict_fail_card_refusals(tmp_path):
    root = base_root(tmp_path)            # 248 built cards
    over = S.report(root, counters={"card_refused": 10, "chain_none": 3, "card_refused:holders_stale": 10}, window_ms=WIN)     # 13/261 = 4.98%
    assert over["verdict"] == "PASS" and over["card_refusals"] == 13
    rep = S.report(root, counters={"card_refused": 10, "chain_none": 4}, window_ms=WIN)     # 14/262 = 5.34%
    assert rep["verdict"] == "FAIL" and "card_refusals_above_5pct_of_attempted" in rep["fail_reasons"]


def test_verdict_fail_run_error_variants(tmp_path):
    root = base_root(tmp_path)
    assert "run_error" in S.report(root, counters={}, run_error="ValueError", window_ms=WIN)["fail_reasons"]
    for k in ("model_exception", "cost_mismatch", "ledger_unreadable", "hook_errors"):
        assert "run_error" in S.report(root, counters={k: 1}, window_ms=WIN)["fail_reasons"], k


def test_verdict_fail_spend_over_cap(tmp_path):
    root = base_root(tmp_path)
    put_cost(root, 0.101)
    rep = S.report(root, counters={}, window_ms=WIN)
    assert rep["verdict"] == "FAIL" and "spend_above_cap" in rep["fail_reasons"] and rep["spend_usd"] == pytest.approx(0.101)
    root2 = base_root(tmp_path / "x")
    put_cost(root2, 0.10)
    assert "spend_above_cap" not in S.report(root2, counters={}, window_ms=WIN)["fail_reasons"]


def test_report_whitelist_never_carries_pool_or_price(tmp_path):
    root = base_root(tmp_path)
    text = S.render(S.report(root, counters={"card_refused": 1}, window_ms=WIN))
    assert "SECRETPOOL" not in text
    for bad in ("price", "fill", "pnl", "pool_id", "mint", "pick", "shown"):
        assert f'"{bad}' not in text, bad


# --------------------------------------------------------------------------------------------------------------------- end to end, fake caller
def test_period_plan_hourly_at_h02_after_one_full_prefetch_window():
    assert S.PREFETCH_LEAD_MS == RN.PREFETCH_WINDOW_END_MS - RN.PREFETCH_WINDOW_START_MS + HOUR and S.T_D_OFFSET_MS == RN.T_D_OFFSET_MS and S.PERIOD_MS == HOUR
    te = RN.T_E_OFFSET_MS - RN.T_D_OFFSET_MS
    for off in range(0, 60 * MIN, 5 * MIN + 17_000):
        t0 = T0 + 5 * HOUR + off
        grid, last_te, t_end = S.period_plan(t0, S.WARMUP_MS, S.PERIOD_MS, S.PERIODS, RN.TICK_MS, te)
        assert len(grid) == 31 and {b - a for a, b in zip(grid, grid[1:])} == {HOUR}
        assert all(g % HOUR == RN.T_D_OFFSET_MS for g in grid)                       # the runner's real instants
        assert grid[0] - HOUR - S.PREFETCH_LEAD_MS >= t0 + S.WARMUP_MS                 # the cold-fill window (and the warm-up) pass unscreened, then a whole window
        assert grid[0] - 2 * HOUR - S.PREFETCH_LEAD_MS < t0 + S.WARMUP_MS             # ... and it is the FIRST such H:02
        assert last_te - grid[0] == 30 * HOUR + 45_000 >= S.MIN_SPAN_MS
        assert 31 * HOUR < t_end - t0 < 31 * HOUR + 3 * HOUR                           # about 32-33 h wall


def test_cli_has_no_period_or_window_override_and_real_chain_needs_the_hourly_grid(tmp_path, monkeypatch):
    src = Path(S.__file__).read_text()
    assert "--period-min" not in src and "--runner-window" not in src and "PREFETCH_WINDOW_START_MS =" not in src and "PREFETCH_WINDOW_START_MS = 0" not in src
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("MAL_HV4_RPC", "helius")
    parent = tmp_path / "smoke"
    parent.mkdir()
    for kw in ({"period_ms": 4 * MIN}, {"anchor_h02": False}):
        with pytest.raises(S.Refused, match="real_chain_requires_hourly_h02_grid"):
            S.run_smoke(rules(), args(parent / "s", **{}), model=None, chain=None, parent=str(parent), **kw)


def test_sim_run_drives_real_rank_calls_on_the_compressed_grid(tmp_path):
    rep, smk, model = sim(tmp_path)
    assert rep["periods"] == 5 and rep["calls"] == 5 and rep["valid_permutations"] == 5 and rep["fallback"] == 0
    assert len(model.calls) == 5 and all(r.kind == "rank" and r.arm == "L" for r in model.calls)       # the real RANK request, one per period
    rows = [json.loads(x) for f in sorted((smk / "root" / "decisions" / "SCAN").glob("*.jsonl")) for x in f.read_bytes().splitlines()]
    td = [r["t_d"] for r in rows if r["kind"] == "period"]
    assert len(td) == 5 and len(set(td)) == 5
    assert rep["span_s"] == pytest.approx(4 * 4 * 60 + 45) and rep["verdict"] == "INCOMPLETE" and "span_under_2h" in rep["incomplete_reasons"]
    assert (smk / "report.json").is_file()


def test_sim_output_has_no_pool_ids_or_prices(tmp_path):
    rep, smk, _ = sim(tmp_path)
    rep = {k: v for k, v in rep.items() if k != "disclosure"}          # the disclosure sentence names "prices" to say they were not opened
    text = S.render(rep) + (smk / "report.json").read_text().split('"disclosure"')[0]
    for pool in POOLS.values():
        assert pool not in text
    for bad in ("price", "fill", "pnl", "entry_px", "exit"):
        assert bad not in text.lower().replace("excluded", ""), bad


def test_sim_never_touches_engine_ledger_or_real_registry(tmp_path):
    rep, smk, _ = sim(tmp_path)
    assert (smk / "smoke-registry.json").is_file() and (smk / "engine-ledger").is_dir()
    src = Path(S.__file__).read_text()
    assert "bots.json" not in src.split('"""', 2)[2]         # the real registry path is only named in the docstring
    assert ".tick(" not in src                      # the frozen hourly tick is not used: compression is by driving screen/enter
    assert "engine.ledger" not in src and "ledger.rows" not in src


def test_spend_stop_before_each_call(tmp_path):
    rep, smk, model = sim(tmp_path, cost=0.0499, periods=6)
    assert rep["spend_usd"] == pytest.approx(0.0998) and rep["spend_usd"] <= 0.10
    assert len(model.calls) == 2 and rep["spend_stop_hits"] == 4          # calls 3..6 never reach the model: 0.0998 + 0.002 > 0.10
    assert rep["calls"] == 6 and rep["fallback"] == 4 and rep["model_error_counts"] == {"smoke_spend_stop": 4}
    assert rep["verdict"] == "FAIL" and "fallback_share_above_5pct" in rep["fail_reasons"] and "spend_above_cap" not in rep["fail_reasons"]


def test_spend_stop_wrapper_unit(tmp_path):
    guard = RN.Hv7Guard(tmp_path / "root", day_cap_usd=0.10)

    class Inner:
        model = "m"

        def __init__(self):
            self.n = 0

        def complete(self, req, now_ms):
            self.n += 1
            put_cost(tmp_path / "root", 0.05)
            return m4.ModelReply(text="x")

    inner = Inner()
    # the helper writes DAY ledgers; the wrapper sums every day file under RANK-L
    w = S.SpendStopModel(inner, guard, tmp_path / "root", 0.10, 0.002)
    guard.day_spent = lambda day: 0.0 if not (tmp_path / "root" / "ledgers" / "RANK-L" / f"{day}.jsonl").exists() else Ledger(tmp_path / "root" / "ledgers" / "RANK-L" / f"{day}.jsonl").spent()
    assert w.complete(None, 0).error is None
    assert w.complete(None, 0).error is None            # spent 0.05 -> second call allowed (0.05 + 0.002 <= 0.10); writes 0.10 total
    stopped = w.complete(None, 0)
    assert stopped.error == "smoke_spend_stop" and w.hits == 1 and inner.n == 2


def test_cap_cannot_be_raised(tmp_path):
    with pytest.raises(S.Refused):
        S.run_smoke(rules(), args(tmp_path / "smoke" / "s", cap_usd=0.11), parent=str(tmp_path / "smoke"))


# --------------------------------------------------------------------------------------------------------------------- refusals and key hygiene
def test_key_never_printed(tmp_path, capsys):
    rep, smk, _ = sim(tmp_path)
    S.sys.stdout.write(S.render(rep))
    out = capsys.readouterr()
    assert FAKE_KEY not in out.out and FAKE_KEY not in out.err
    for f in smk.rglob("*"):
        if f.is_file():
            assert FAKE_KEY.encode() not in f.read_bytes(), f
    src = Path(S.__file__).read_text()
    assert "print(" not in src.replace('print(f"hv7_smoke: refused: {e}", file=sys.stderr)', "").replace(
        'print("hv7_smoke: refused: --expect-sha must be 40 hex characters", file=sys.stderr)', "")
    assert "environ[" not in src and 'getenv("ANTHROPIC' not in src


def test_refused_without_key_and_message_is_a_fixed_code(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    parent = tmp_path / "smoke"
    parent.mkdir()
    with pytest.raises(S.Refused, match="api_key_not_in_env"):
        S.run_smoke(rules(), args(parent / "s"), parent=str(parent))
    assert not (parent / "s").exists()           # refused before anything is created


def test_smoke_dir_rules(tmp_path):
    parent = tmp_path / "smoke"
    parent.mkdir()
    (parent / "taken").mkdir()
    for bad in (parent / "taken", parent / "a" / "b", tmp_path / "other", parent / "my-probe"):
        with pytest.raises(S.Refused):
            S.check_smk(str(bad), str(parent))
    assert S.check_smk(str(parent / "ok"), str(parent)) == parent / "ok"


def test_period_args_are_bounded(tmp_path):
    parent = tmp_path / "smoke"
    parent.mkdir()
    for kw in ({"periods": 0}, {"periods": 41}):
        with pytest.raises(S.Refused):
            S.run_smoke(rules(), args(parent / "s", **kw), model=object(), chain=object(), parent=str(parent))


# --------------------------------------------------------------------------------------------------------------------- wrapper
WRAP = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "hv7-smoke.sh"


def wrap(env, *a):
    e = {"PATH": "/usr/bin:/bin", **env}
    return subprocess.run(["sh", str(WRAP), *a], capture_output=True, text=True, env=e, timeout=60)


def test_wrapper_requires_final_sha_and_clean_checkout(tmp_path):
    assert "HV7_FINAL_SHA_required" in wrap({}, str(tmp_path), "x").stdout
    assert "40_hex" in wrap({"HV7_FINAL_SHA": "abcdef1234"}, str(tmp_path), "x").stdout
    repo = tmp_path / "repo"
    repo.mkdir()
    g = ["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f").write_text("1")
    subprocess.run([*g, "add", "f"], check=True)
    subprocess.run([*g, "commit", "-qm", "c"], check=True)
    head = subprocess.run([*g, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert "repo_not_at_final_sha" in wrap({"HV7_FINAL_SHA": "b" * 40}, str(repo), "x").stdout
    (repo / "f").write_text("2")
    r = wrap({"HV7_FINAL_SHA": head}, str(repo), "x")
    assert r.returncode == 2 and "dirty_repo" in r.stdout
    assert wrap({"HV7_FINAL_SHA": head}, str(repo), "bad/tag").returncode == 2


def test_wrapper_text_pins():
    t = WRAP.read_text()
    assert "--cap-usd 0.10" in t and "--periods 31" in t and "--period-min" not in t
    assert '. "$KEYFILE"' in t and "echo" not in t.split('. "$KEYFILE"')[1]          # nothing is echoed after the key is sourced
    assert "ANTHROPIC_API_KEY" not in t           # the wrapper never names the variable's value anywhere


# --------------------------------------------------------------------------------------------------------------------- the smoke uses the runner's chain path

def test_smoke_obtains_its_chain_from_the_runners_live_chain_source_and_its_watch_tick(tmp_path, monkeypatch):
    seen = {"roots": [], "started": 0, "stopped": 0, "watch": 0}

    class _Pre:
        def start(self):
            seen["started"] += 1

        def stop(self):
            seen["stopped"] += 1

        def stats(self):
            return {"calls_last_hour": 0}

    class WatchChain(FakeChain):
        prefetch = _Pre()

        def watch(self, items, hot, now, vols=None):
            seen["watch"] += 1
            assert len(items) <= RN.WATCH_MAX

    def fake_source(root, env=None):
        seen["roots"].append(Path(root))
        return WatchChain()

    monkeypatch.setattr(RN, "live_chain_source", fake_source)
    monkeypatch.setenv("MAL_HV4_RPC", "helius")
    monkeypatch.delenv("MAL_HV4_CHAIN_STATE", raising=False)
    tape = tmp_path / "tape"
    write_tape(tape, tape_rows())
    parent = tmp_path / "smoke"
    parent.mkdir()
    rep = S.run_smoke(rules(), args(parent / "s1", periods=2), model=lambda g: A.StubModel(leader_first, guard=g, cost_usd=0.0, latency_ms=50), chain=None,
                      tape_dir=str(tape), live=False, start_ms=T0 + HOUR, parent=str(parent), verify_sha=False, engine_kw={"sps_fn": lambda: 0.4})
    assert seen["roots"] == [parent / "s1" / "root"]                        # built by the runner's function, rooted under the smoke dir
    assert seen["started"] == 1 and seen["stopped"] == 1 and seen["watch"] >= 1       # watch fed by Hv7Runner.watch_tick
    assert rep["chain_source"].startswith("hv7_runner.live_chain_source")
    assert not hasattr(S, "_feed_watch")                                     # no private watch-set construction left


# --------------------------------------------------------------------------------------------------------------------- production setup path (job #1776 crashed here)

def _origin_clone(tmp_path):
    """A local bare origin with one commit on main, and a clone of it: verify_sha_on_origin runs for real (git fetch of a local path, no network)."""
    bare, work = tmp_path / "origin.git", tmp_path / "work"
    git = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run([*git, "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    (work / "f").write_text("1")
    subprocess.run([*git, "-C", str(work), "add", "f"], check=True)
    subprocess.run([*git, "-C", str(work), "commit", "-qm", "c"], check=True)
    subprocess.run([*git, "-C", str(work), "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
    return work, subprocess.run([*git, "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def test_production_setup_path_with_the_test_env_unset(tmp_path, monkeypatch):
    """No now_ms, no verify_sha=False, MAL_ARENA_TEST unset (as the wrapper leaves it): the real clock and the real sha verification must work end to end."""
    monkeypatch.delenv("MAL_ARENA_TEST", raising=False)
    work, head = _origin_clone(tmp_path)
    tape = tmp_path / "tape"
    write_tape(tape, tape_rows())
    parent = tmp_path / "smoke"
    parent.mkdir()
    R = rules()
    R.repo = str(work)
    a = args(parent / "s1", periods=2, frozen_sha=head)
    rep = S.run_smoke(R, a, model=lambda g: A.StubModel(leader_first, guard=g, cost_usd=0.0, latency_ms=50), chain=FakeChain(), tape_dir=str(tape), live=False,
                      sleep=lambda s: None, parent=str(parent), engine_kw={"sps_fn": lambda: 0.4}, period_ms=4 * MIN, anchor_h02=False)
    reg = [json.loads(x) for x in (parent / "s1" / "smoke-registry.json").read_text().splitlines()]
    lines = [r for r in reg if r.get("kind") == "register"]
    assert len(lines) == 4 and all(r["time_source"] == "wall_clock" and r["verified_sha"] == head for r in lines)       # the production line, not a test_override
    assert rep.get("run_error") is None and rep["periods"] == 2 and not rep["fail_reasons"], rep["fail_reasons"]     # no calls: the fixture tape is in the past, setup and loop are what is tested


def test_production_refusal_is_active_for_the_test_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("MAL_ARENA_TEST", raising=False)
    parent = tmp_path / "smoke"
    parent.mkdir()
    with pytest.raises(AP.ArenaRefused, match="test-only overrides"):
        S.run_smoke(rules(), args(parent / "s1", periods=2), model=object(), chain=FakeChain(), tape_dir=str(tmp_path), live=False, start_ms=T0 + HOUR, parent=str(parent))
