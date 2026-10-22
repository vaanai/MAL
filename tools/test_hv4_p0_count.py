"""tools/hv4_p0_count.py on synthetic tapes. No network, no model, no key, no host file. The synthetic pools are the HV4 runner test's own (test_hv4_runner.trade_rows).

  * counts: a pullback pool is counted for CON (and only CON), per scan, with the chain vetoes reported as not applied;
  * as-of: scans up to T give the same counts on a tape cut at T - 1 as on the whole tape; a state that does not stop at T - 1 raises AsOfError;
  * the P1 projection drops every field that is not on the list (creator, name, symbol, signature), and the report prints no mint, pool or trader;
  * the CLI refuses (exit 2) a repo that is dirty or not at the pinned sha, and a day without warm-up tape FAILS (exit 1);
  * prescreen on and off give the same counts (the equivalence the runner relies on).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import arena_state as AS
from tools import hv4_cards as C
from tools import hv4_p0_count as P
from tools.test_hv4_runner import MINTS, POOLS, T0, WSOL, trade_rows, write_tape

HOUR = 3_600_000
DAY0 = T0 - 6 * HOUR  # 00:00Z of the fixture's day (T0 is 06:00Z)
DAY = P.day_label(DAY0)
R = SimpleNamespace(AS=AS, C=C, repo=str(Path(__file__).resolve().parent.parent))
SCRIPT = Path(P.__file__).resolve()


def fill_hours(d: Path, start: int, end: int) -> None:
    h = start - start % HOUR
    while h < end:
        p = d / f"tape-{P.hour_key(h)}.jsonl"
        if not p.exists():
            p.write_text("")
        h += HOUR


def make_tape(tmp: Path, rows: list[dict] | None = None, warm_hours: bool = True) -> Path:
    d = tmp / "tape"
    write_tape(d, trade_rows() if rows is None else rows)
    fill_hours(d, DAY0 - (365 * 60_000 if warm_hours else 0), DAY0 + 24 * HOUR)
    return d


def count(tape: Path, end_ms: int = DAY0 + 24 * HOUR, prescreen: bool = True, per_scan: list | None = None):
    st = P.make_state_class(AS)(str(tape), start_ms=DAY0 - 365 * 60_000)
    P._GAP_EXTRA = tuple(AS.GAP_EXTRA)
    return P.count_window(R, st, C.SetupLimiter(), DAY, DAY0, end_ms, prescreen=prescreen, per_scan=per_scan)


def test_pullback_pool_is_counted_for_con_only(tmp_path):
    rec, dc = count(make_tape(tmp_path))
    assert rec["scans"] == 288 and rec["status"] == "OK"
    con, big = dc.persona["CON"], dc.persona["BIG"]
    assert con["tags_top"] >= 1 and con["tag_cards_ok"] >= 1
    assert big["tags_top"] == 0 and big["tag_cards_ok"] == 0
    rows = P.arm_rows(DAY, dc)
    by = {r["arm"]: r["attempts_ub"] for r in rows}
    assert by["CON-X"] == con["tag_cards_ok"] and by["CON-L"] == con["tag_cards_ok"] + con["board_cards_ok"] == by["CON-R"] == by["CON-P"] == by["CON-F"]
    assert by["BIG-X"] == big["tag_cards_ok"] == 0 and by["BIG-L"] == by["BIG-R"] == by["BIG-F"] == big["board_cards_ok"] + big["tag_cards_ok"]
    assert len(rows) == 9


def test_prescreen_on_and_off_give_the_same_counts(tmp_path):
    tape = make_tape(tmp_path)
    a, da = count(tape, end_ms=DAY0 + 8 * HOUR, prescreen=True)
    b, db = count(tape, end_ms=DAY0 + 8 * HOUR, prescreen=False)
    for pr in P.PERSONAS:
        assert da.persona[pr] == db.persona[pr] and da.tape_veto[pr] == db.tape_veto[pr]
    assert a["pool_scans_filter_pass"] == b["pool_scans_filter_pass"]
    assert a["v_missing"] == b["v_missing"] and a["v_band"] == b["v_band"]


def test_as_of_cut_scans_up_to_T_do_not_see_rows_from_T(tmp_path):
    rows = trade_rows()
    T_cut = T0 + 10 * 60_000 + P.SCAN_SECOND_MS  # the first scan at which the pullback shows
    full = make_tape(tmp_path / "full", rows)
    cut = make_tape(tmp_path / "cut", [r for r in rows if r["t_recv_ms"] < T_cut])
    a, b = [], []
    count(full, per_scan=a)
    count(cut, per_scan=b)
    pa = {T: v for T, v in a if T <= T_cut}
    pb = {T: v for T, v in b if T <= T_cut}
    assert pa == pb and len(pa) > 50
    assert pa[T_cut]["CON"][0] >= 1  # the pullback is already visible at T_cut from rows before it
    # a row AT the scan instant is not applied before that scan: adding one changes nothing at or before T_cut
    extra = dict(rows[-1], t_recv_ms=T_cut, signature="zz", side="buy", sol_lamports=900 * 10 ** 9, token_raw=1)
    odd = make_tape(tmp_path / "odd", rows + [extra])
    c = []
    count(odd, per_scan=c)
    assert {T: v for T, v in c if T <= T_cut} == pa


def test_state_that_ignores_the_cut_raises_as_of_error(tmp_path, monkeypatch):
    tape = make_tape(tmp_path)
    cls = P.make_state_class(AS)
    orig = cls.poll
    monkeypatch.setattr(cls, "poll", lambda self, max_rows=None, until_ms=None: orig(self, max_rows, until_ms=None))
    st = cls(str(tape), start_ms=DAY0 - 365 * 60_000)
    with pytest.raises(P.AsOfError):
        P.count_window(R, st, C.SetupLimiter(), DAY, DAY0 + 6 * HOUR, DAY0 + 7 * HOUR)


def test_projection_keeps_only_the_p1_fields_and_nothing_identifying_is_printed(tmp_path):
    row = {"v": 1, "type": "create", "prog": "pumpfun", "t_recv_ms": 5, "mint": "MintXYZ", "creator": "CreatorSecret", "trader": "T1", "name": "NameSecret",
           "symbol": "SYM", "signature": "sig", "event_index": 0, "slot": 7, "quote_reserve": 1, "base_reserve": 2, "event_ts": 1}
    out = P.project_row(row)
    assert set(out) == {"type", "prog", "t_recv_ms", "mint", "trader", "creator", "slot", "quote_reserve", "base_reserve"}
    assert P.project_row({"type": "weird", "prog": "x", "t_recv_ms": 1, "mint": "m", "trader": "w"}) == {"type": "weird", "prog": "x", "t_recv_ms": 1}
    tape = make_tape(tmp_path)
    st = P.make_state_class(AS)(str(tape), start_ms=DAY0 - 365 * 60_000)
    st.poll(until_ms=DAY0 + 8 * HOUR)
    assert all(p.name is None and p.symbol is None for p in st.pools()) and len(st) > 0
    rec, dc = count(tape)
    report = P.render(P.build_report({"kind": "header"}, [(rec, dc)]))
    for forbidden in (*MINTS.values(), *POOLS.values(), "B00", "S00", "signature", "trader", "mint\":"):
        assert forbidden not in report
    last = report.strip().splitlines()[-1]
    body = report[: report.rindex("sha256 ")]
    assert last == "sha256 " + hashlib.sha256(body.encode()).hexdigest()


def test_gap_counts_union_and_clip():
    spans = {("pumpswap", "slot_jump", 100_000): [400_000, True], ("pumpswap", "socket_reconnect", 300_000): [600_000, True], ("pumpfun", "slot_jump", 0): [50, False],
             ("all", "x", 10 ** 9): [10 ** 9 + 5, True]}
    g = P.gap_counts(spans, 200_000, 500_000)
    assert g["data_loss_s"] == {"pumpswap": 300.0} and g["data_loss_spans"] == {"pumpswap:slot_jump": 1, "pumpswap:socket_reconnect": 1}
    assert g["pumpswap_data_loss_le_1h"] is True
    big = P.gap_counts({("pumpswap", "k", 0): [4_000_000, True]}, 0, 10 ** 7)
    assert big["pumpswap_data_loss_le_1h"] is False


def test_floor_and_verdict():
    dc = P.DayCounts()
    for pr in P.PERSONAS:
        dc.persona[pr]["tag_cards_ok"] = 3
    rec = {"kind": "day", "day": "d1", "status": "OK"}
    rep = P.build_report({"kind": "header"}, [(rec, dc)])
    v = [r for r in rep if r["kind"] == "verdict"][0]
    assert v["floor_pass_all_9_arms"] is True and "upper bound" in v["disclosure"]
    dc.persona["BIG"]["tag_cards_ok"] = 1
    v = [r for r in P.build_report({"kind": "header"}, [(rec, dc)]) if r["kind"] == "verdict"][0]
    assert v["floor_pass_all_9_arms"] is False
    fail = {"kind": "day", "day": "d2", "status": "FAIL", "reason": "x"}
    v = [r for r in P.build_report({"kind": "header"}, [(rec, dc), (fail, None)]) if r["kind"] == "verdict"][0]
    assert v["all_days_ok"] is False and v["floor_pass_all_9_arms"] is False


def test_scan_instants_are_the_runner_cadence():
    s = P.scan_instants(DAY0, DAY0 + 24 * HOUR)
    assert s[0] == DAY0 + 2000 and s[1] - s[0] == 300_000 and len(s) == 288 and all(t % 300_000 == 2000 for t in s)


# ------------------------------------------------------------------------------------------------------------------------------ CLI and repo checks
@pytest.fixture()
def clean_repo(tmp_path):
    """A clean one-commit git repo holding the rule files (a stand-in for the checkout at the frozen sha)."""
    root = Path(R.repo)
    repo = tmp_path / "rules"
    (repo / "tools").mkdir(parents=True)
    for f in ("__init__.py", "arena_state.py", "arena_zst.py", "hv4_cards.py", "paper_curve_math.py"):
        shutil.copy(root / "tools" / f, repo / "tools" / f)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return repo, sha


def cli(args: list[str]) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, "MAL_ARENA_TEST": "1"} if "--expect-sha" in args else dict(os.environ)
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, timeout=600, env=env)


def test_cli_refuses_wrong_sha_and_dirty_repo(tmp_path, clean_repo):
    repo, sha = clean_repo
    tape = make_tape(tmp_path)
    base = ["--repo", str(repo), "--tape-dir", str(tape), "--days", DAY]
    r = cli(base)  # the pinned sha is the default: this stand-in repo is not at it
    assert r.returncode == 2 and "pinned sha" in r.stderr and r.stdout == ""
    (repo / "tools" / "extra.py").write_text("x = 1\n")  # untracked file: dirty
    r = cli([*base, "--expect-sha", sha])
    assert r.returncode == 2 and "dirty" in r.stderr and r.stdout == ""
    import os

    r = subprocess.run([sys.executable, "-I", str(SCRIPT), *base, "--expect-sha", sha], capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "MAL_ARENA_TEST"})
    assert r.returncode == 2 and "MAL_ARENA_TEST" in r.stderr


def test_projection_gives_the_same_verdicts_as_a_plain_state_with_a_creator_selling(tmp_path):
    rows = trade_rows()
    cr = "CreatorWallet" + "1" * 30
    rows = [{"v": 1, "type": "create", "prog": "pumpfun", "slot": 1, "t_recv_ms": T0 - 4 * HOUR, "signature": f"c{k}", "event_index": 0, "mint": m, "creator": cr,
             "trader": cr, "quote_reserve": 30 * 10 ** 9, "base_reserve": 10 ** 15} for k, m in MINTS.items()] + rows
    n = 0
    for r in rows:  # make the creator one of the sellers
        if r["type"] == "trade" and r["side"] == "sell":
            n += 1
            if n % 2:
                r["trader"] = cr
    tape = make_tape(tmp_path, rows)
    base = AS.ArenaState(str(tape), start_ms=DAY0 - 365 * 60_000)
    prj = P.make_state_class(AS)(str(tape), start_ms=DAY0 - 365 * 60_000)
    T = T0 + 30 * 60_000 + 2000
    base.poll(until_ms=T - 1)
    prj.poll(until_ms=T - 1)
    chain = {"as_of_ms": T - 1, "as_of_slot": prj.slot, "mint_authority": None, "freeze_authority": None, "token_program": C.SPL_TOKEN_PROGRAM, "extensions": [], "mayhem": False}
    seen = 0
    for p in base.pools_wsol(lambda p: p.prog == "pumpswap"):
        q = prj.pool(p.id)
        out = []
        for st, pp in ((base, p), (prj, q)):
            snap = st.snapshot(pp.id, candles=False)
            pr = C.norm_prints(list(pp.events))
            vr = C.code_vetoes(snap, pr, chain, as_of_ms=T, as_of_slot=prj.slot)
            out.append((snap["creator"], vr.reasons, vr.checks["sell_route"]))
        assert out[0] == out[1]
        seen += int(out[0][0] == cr)
    assert seen >= 1


def test_cli_runs_from_the_repo_it_is_pointed_at_and_prints_counts_only(tmp_path, clean_repo):
    repo, sha = clean_repo
    tape = make_tape(tmp_path)
    out = tmp_path / "out.txt"
    r = cli(["--repo", str(repo), "--tape-dir", str(tape), "--days", DAY, "--expect-sha", sha, "--out", str(out)])
    assert r.returncode == 0, r.stderr[-400:]
    assert out.read_text() == r.stdout
    rows = [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")]
    assert rows[0]["kind"] == "header" and rows[0]["repo_sha"] == sha
    day = [x for x in rows if x["kind"] == "day"][0]
    assert day["status"] == "OK" and day["scans"] == 288
    con = [x for x in rows if x["kind"] == "persona_day" and x["persona"] == "CON"][0]
    assert con["tag_cards_ok"] >= 1 and con["chain_veto_not_applied"] == con["tag_cards_ok"] + con["board_cards_ok"]
    for forbidden in (*MINTS.values(), *POOLS.values()):
        assert forbidden not in r.stdout and forbidden not in r.stderr
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout.strip() == ""


def test_cli_day_without_warmup_tape_fails_with_exit_1(tmp_path, clean_repo):
    repo, sha = clean_repo
    tape = make_tape(tmp_path, warm_hours=False)
    r = cli(["--repo", str(repo), "--tape-dir", str(tape), "--days", DAY, "--expect-sha", sha])
    assert r.returncode == 1
    day = [json.loads(x) for x in r.stdout.splitlines() if x.startswith('{"') and '"kind":"day"' in x][0]
    assert day["status"] == "FAIL" and day["reason"].startswith("warmup_tape_missing_hours=")
    v = [json.loads(x) for x in r.stdout.splitlines() if '"kind":"verdict"' in x][0]
    assert v["floor_pass_all_9_arms"] is False


def test_tape_check_names_the_reason_without_paths(tmp_path):
    d = make_tape(tmp_path)
    assert P.tape_check(str(d), DAY0, DAY0 + 24 * HOUR, 365) is None
    (d / f"tape-{P.hour_key(DAY0 + 5 * HOUR)}.jsonl").unlink()
    assert P.tape_check(str(d), DAY0, DAY0 + 24 * HOUR, 365) == "day_tape_missing_hours=1"
