"""arena_am6_cut + arena_am6_read: s14 parity with arena_score, excluded attempts absent from the scored set, NOT_DECIDABLE short-circuit, refusals, determinism.
Synthetic ledgers and hour tables only."""
from __future__ import annotations

import collections
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools import arena_am6_cut as C  # noqa: E402
from tools import arena_am6_exclusions as X  # noqa: E402
from tools import arena_am6_read as R  # noqa: E402
from tools import arena_score as S  # noqa: E402
from tools.arena_paper import TEST_ENV, Ledger  # noqa: E402
from tools.test_arena_score import DAY, DAY0, good_pnls, open_row, refused_row, register_bot, start_row, trade_row  # noqa: E402

os.environ[TEST_ENV] = "1"
H = 3_600_000
AS_OF = DAY0 + 30 * DAY


@pytest.fixture(autouse=True)
def _disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def hour_s(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def health(bad=None, days=10):
    """bad: {absolute hour ms: [labels]}; every other hour of `days` days from DAY0 is healthy."""
    bad = bad or {}
    lines = [X.canon({"hour": hour_s(DAY0 + i * H), "excluded": (DAY0 + i * H) in bad, "labels_failing": sorted(bad.get(DAY0 + i * H, []))}) for i in range(days * 24)]
    sha = hashlib.sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    return "\n".join(lines + [X.canon({"type": "sha256", "hours": len(lines), "sha256": sha})]) + "\n"


def shift(row, entry_ms, dur_ms=5000):
    d = dict(row["decision_ms"])
    d["entry"] = entry_ms
    d["exit"] = None if row["kind"] == "open" else entry_ms + dur_ms
    return dict(row, decision_ms=d)


def book(n=105, per=21, entry_of=None):
    """n positions, `per` per UTC date, decided at hour 1+(i % per) of the date, each with an open row and a trade row at the 0.25 stake."""
    rows = [start_row()]
    for i, p in enumerate(good_pnls(n)):
        pid, day = f"p{i:04d}", i // per
        ms = DAY0 + day * DAY + (1 + i % per) * H + 100 + i
        ms = entry_of(i, ms) if entry_of else ms
        rows += [shift(open_row(pid, i, day), ms), shift(trade_row(pid, i, day, p), ms)]
    return rows


def sha_of(text):
    return json.loads(text.rstrip("\n").split("\n")[-1])["sha256"]


def RR(root, reg, bot, hl, as_of, restarts="", shas=None, **kw):
    """R.run with the notebooked table sha(s) derived from the table text(s) unless `shas` is given."""
    hls = [hl] if isinstance(hl, str) else list(hl)
    return R.run(root, reg, bot, hls, as_of, restarts, shas if shas is not None else [sha_of(t) for t in hls], **kw)


def setup(tmp_path, rows):
    reg = tmp_path / "bots.json"
    register_bot("botA", "abc1234", DAY0, reg, deciding_stake=0.25, model_ids=[], now_ms=0)
    led = Ledger(tmp_path / "ledger")
    for r in rows:
        led.append(r)
    return str(tmp_path / "ledger"), str(reg)


def go(tmp_path, rows, hl=None, restarts="", as_of=AS_OF, **kw):
    root, reg = setup(tmp_path, rows)
    return RR(root, reg, "botA", hl or health(), as_of, restarts, **kw), root, reg


def test_cut_carries_only_the_five_keys_and_is_deterministic(tmp_path):
    rows = book()
    root, reg = setup(tmp_path, rows)
    outs = []
    for k in (1, 2):
        out = tmp_path / f"cut{k}.jsonl"
        assert C.main(["--ledger-root", root, "--registry", reg, "--bot", "botA", "--as-of-ms", str(AS_OF), "--out", str(out)]) == 0
        outs.append(out.read_text())
    assert outs[0] == outs[1]
    recs = [json.loads(x) for x in outs[0].splitlines()]
    assert len(recs) == 105 and all(set(r) == set(X.ALLOWED) for r in recs)
    assert not any(f in outs[0].lower() for f in ("pnl", "price", "stake", "net_lamports", "mint"))
    lines, shaline = C.render_cut(C.attempts_from_rows(rows, DAY0, 0.25, AS_OF))
    assert json.loads(shaline)["sha256"] == hashlib.sha256(outs[0].encode()).hexdigest()
    X.run(health(), outs[0], "", AS_OF)  # the exclusions tool takes the file unchanged


def test_cut_exit_open_and_refused_fields():
    rows = book(5, 5) + [refused_row(1, 0, reason="stale_decision")]
    rows = [r for r in rows if not (r["kind"] == "trade" and r["position_id"] == "p0002")]  # p0002 stays open
    rows += [dict(trade_row("p0003", 3, 0, 1e6), kind="excluded", exclusion_reason="engine_restart", legs=None, calc=None)]
    rows = [r for r in rows if not (r["kind"] == "trade" and r["position_id"] == "p0003")]
    a = {x["attempt_id"]: x for x in C.attempts_from_rows(rows, DAY0, 0.25)}
    assert a["p0002"]["exit_ms"] is None and a["p0000"]["exit_ms"] == a["p0000"]["decided_ms"] + 5000
    assert a["ref1"]["exit_ms"] == a["ref1"]["decided_ms"] and a["ref1"]["s14_excluded"] is False
    assert a["p0003"]["open_at_restart"] is True and a["p0003"]["s14_excluded"] is True


def s14_rows():
    rows = book()
    rows = [dict(r, kind="excluded", exclusion_reason="feed_gap", legs=None, calc=None) if r["kind"] == "trade" and r["position_id"] == "p0001" else r for r in rows]
    rows = [dict(r, v_src={"entry": "none", "exit": "row", "span": {"none": 1}}) if r["kind"] == "trade" and r["position_id"] == "p0002" else r for r in rows]
    rows += [{"schema": "arena_trade_v1", "kind": "late_print", "trade_id": "p0003-late-x", "position_id": "p0003", "bot_id": "botA",
              "decision_ms": {"entry": DAY0 + 1000, "exit": None}, "gap_overlap": True},
             {"schema": "arena_trade_v1", "kind": "late_print", "trade_id": "p0004-late-x", "position_id": "p0004", "bot_id": "botA",
              "decision_ms": {"entry": DAY0 + 1000, "exit": None}, "gap_overlap": False},
             refused_row(1, 1, reason="no_prints"), refused_row(2, 1, reason="stale_decision")]
    return rows


def test_s14_parity_with_the_scorer_on_fixtures(tmp_path):
    rows = s14_rows()
    a = {x["attempt_id"]: x["s14_excluded"] for x in C.attempts_from_rows(rows, DAY0, 0.25)}
    assert {k for k, v in a.items() if v} == {"p0001", "p0002", "p0003", "ref1"}  # stale_decision and gap_overlap false are not s14
    root, reg = setup(tmp_path, rows)
    g = C.parity_guard("botA", S.load_registry(reg), Ledger(root).scan_bot("botA")[0], AS_OF, 0.25)
    assert g["ok"] and g["scorer"]["excluded"] == {"feed_gap": 1, "missing_v": 1, "late_print_gap": 1} and g["scorer"]["refused_data"] == 1
    assert g["cutter"] == g["scorer"]


def test_parity_guard_detects_a_drifted_mirror(tmp_path, monkeypatch):
    root, reg = setup(tmp_path, s14_rows())
    rows = Ledger(root).scan_bot("botA")[0]
    real = C.s14_reasons
    monkeypatch.setattr(C, "s14_reasons", lambda rs, st: {k: v for k, v in real(rs, st).items() if v != "missing_v"})
    assert not C.parity_guard("botA", S.load_registry(reg), rows, AS_OF, 0.25)["ok"]


def test_wrapper_refuses_when_the_mirror_drifts(tmp_path, monkeypatch):
    root, reg = setup(tmp_path, s14_rows())
    real = C.s14_reasons
    monkeypatch.setattr(C, "s14_reasons", lambda rs, st: {k: v for k, v in real(rs, st).items() if v != "missing_v"})
    with pytest.raises(R.Refused, match="parity"):
        RR(root, reg, "botA", health(), AS_OF)


def test_healthy_book_matches_the_direct_scorer_and_excluded_hour_attempts_are_absent(tmp_path):
    rows = book()
    (code, lines, res), root, reg = go(tmp_path, rows)
    assert code == 0 and res["bots"]["botA"]["attempts"] == 105  # nothing excluded: identical to the scorer's own read
    direct = S.score_all(root, reg, now_ms=AS_OF)["bots"]["botA"]
    assert res["bots"]["botA"]["books"] == direct["books"] and res["bots"]["botA"]["status"] == direct["status"]
    # one attempt (p0010) decided in an excluded decision-input hour: absent from attempts and trades, and not in the boundary count
    bad_hour = (DAY0 + (1 + 10) * H)
    sub = tmp_path / "x"
    sub.mkdir()
    (code, lines, res), *_ = go(sub, book(), hl=health({bad_hour: ["decision-input"]}))
    b = res["bots"]["botA"]
    assert b["attempts"] == 104 and b["books"]["0.25"]["n_trades"] == 104 and b["rows_total"] == 105 * 2 + 1 - 2
    assert any(x.startswith("exclusions {") and '"type":"sha256"' in x for x in lines)
    tot = json.loads(next(x for x in lines if x.startswith("exclusions_totals ")).split(" ", 1)[1])
    assert tot["n_excl_10pct_class"] == 1 and tot["n_counted"] == 104 and tot["verdict_cap"] == "OK"


def test_span_overlap_attempt_is_not_a_priced_trade_but_still_an_attempt(tmp_path):
    # p0000 is decided 2 s before the end of day0 hour 22 (healthy) and exits 3 s into hour 23, a price-path excluded hour that holds no decision
    rows = [shift(r, DAY0 + 23 * H - 2000, 5000) if r.get("position_id") == "p0000" else r for r in book()]
    (code, lines, res), *_ = go(tmp_path, rows, hl=health({DAY0 + 23 * H: ["price-path"]}))
    b = res["bots"]["botA"]
    assert b["attempts"] == 105 and b["books"]["0.25"]["n_trades"] == 104 and b["books"]["0.25"]["excluded"] == {"am6_span_overlap_pricepath": 1}
    tot = json.loads(next(x for x in lines if x.startswith("exclusions_totals ")).split(" ", 1)[1])
    assert tot["n_excl_span_overlap_pricepath"] == 1 and tot["verdict_cap"] == "OK"


def test_not_decidable_short_circuits_without_calling_the_scorer(tmp_path, monkeypatch):
    bad = {DAY0 + d * DAY + (1 + k) * H: ["decision-input"] for d in (0, 1) for k in range(7)}  # 14 of 130 attempts (> 10%); the other 116 still reach the boundary
    calls = []
    real = S.score_bot
    monkeypatch.setattr(S, "score_bot", lambda *a, **k: calls.append(1) or real(*a, **k))
    (code, lines, res), *_ = go(tmp_path, book(130), hl=health(bad))
    assert code == 0 and res is None
    assert len(calls) == 1  # only the parity guard (R2: it runs BEFORE the verdict); the scored read is never made
    assert any("NOT_DECIDABLE_10PCT" in x for x in lines) and not any("ARENA GATE READ" in x for x in lines)


def test_unclosed_old_attempt_keeps_the_scorers_orphan_rule(tmp_path):
    rows = [r for r in book() if not (r["kind"] == "trade" and r["position_id"] == "p0007")]
    (code, lines, res), *_ = go(tmp_path, rows, hl=health(days=31))  # an open attempt is span-checked through the as-of hour
    assert res["bots"]["botA"]["status"] == "NOT_DECIDABLE" and res["bots"]["botA"]["restart_orphans"] == 1


def test_not_ready_before_100_attempts(tmp_path):
    (code, lines, res), *_ = go(tmp_path, book(60, 12))
    assert res is None and any(x.startswith("NOT_READY") for x in lines)


def test_refusals(tmp_path):
    root, reg = setup(tmp_path, book())
    tampered = health().replace('"excluded":false', '"excluded":true', 1)
    with pytest.raises(X.Refused, match="sha256"):
        RR(root, reg, "botA", tampered, AS_OF)
    with pytest.raises(R.Refused, match="expected"):
        RR(root, reg, "botA", health(), AS_OF, shas=["0" * 64])
    with pytest.raises(R.Refused, match="not in the registry"):
        RR(root, reg, "nobody", health(), AS_OF)
    with pytest.raises(X.Refused, match="absent from health table"):  # a decision hour the table does not cover is unmeasurable
        RR(root, reg, "botA", health(days=2), AS_OF)
    assert R.main(["--ledger-root", root, "--registry", reg, "--bot", "botA", "--health", str(tmp_path / "missing"), "--health-sha", "x", "--restarts",
                   str(tmp_path / "missing"), "--as-of-ms", str(AS_OF)]) == 2
    with pytest.raises(X.Refused, match="forbidden"):  # the exclusions tool still refuses a price key in a hand-made cut
        X.run(health(), json.dumps({"attempt_id": "a", "decided_ms": DAY0 + H, "exit_ms": None, "s14_excluded": False, "pnl_sol": 1}) + "\n", "", AS_OF)


def test_cut_refuses_a_torn_ledger(tmp_path):
    root, reg = setup(tmp_path, book())
    f = next(Path(root, "botA").glob("trades-*.jsonl"))
    f.write_text(f.read_text() + '{"kind":"trade", torn')
    assert C.main(["--ledger-root", root, "--registry", reg, "--bot", "botA", "--as-of-ms", str(AS_OF), "--out", str(tmp_path / "o")]) == 2
    code, lines, res = RR(root, reg, "botA", health(), AS_OF)
    assert res is None and any("NOT_DECIDABLE_LEDGER" in x for x in lines)


def test_wrapper_output_is_deterministic(tmp_path):
    root, reg = setup(tmp_path, s14_rows())
    a = RR(root, reg, "botA", health(), AS_OF)[1]
    b = RR(root, reg, "botA", health(), AS_OF)[1]
    assert a == b


def test_scorer_file_is_not_touched():
    import subprocess
    diff = subprocess.run(["git", "diff", "--name-only", "origin/main", "--", "tools/arena_score.py", "tools/arena_am6_exclusions.py", "tools/arena_hour_health.py"],
                          cwd=str(Path(__file__).parent.parent), capture_output=True, text=True).stdout
    assert diff.strip() == ""


def late_book():
    """110 attempts, 21 per date; p0107 is on date 5, AFTER the scorer's natural boundary (end of date 4) on unmodified rows, and has no V."""
    return [dict(r, v_src={"entry": "none", "exit": "row", "span": {"none": 1}}) if r["kind"] == "trade" and r["position_id"] == "p0107" else r for r in book(110)]


def six_bad_hours():
    return health({DAY0 + (1 + k) * H: ["decision-input"] for k in range(6)})  # 6 of 110 attempts decided in excluded hours of date 0


def test_parity_guard_runs_over_the_rows_scored_through_the_wrappers_boundary(tmp_path, monkeypatch):
    rows = late_book()
    root, reg = setup(tmp_path, rows)
    led = Ledger(root).scan_bot("botA")[0]
    natural = S.score_bot("botA", S.load_registry(reg), led, AS_OF)["read_boundary_ms"]
    (code, lines, res), *_ = go(tmp_path / "w", rows, hl=six_bad_hours()) if (tmp_path / "w").mkdir() is None else None
    b = int(next(x for x in lines if x.startswith("read_boundary_ms")).split()[1])
    assert b > natural  # removing the excluded-hour attempts moved the boundary later
    dropped = [f"p{i:04d}" for i in range(6)]
    g = C.parity_guard("botA", S.load_registry(reg), led, AS_OF, 0.25, dropped)
    assert g["boundary_ms"] == b and g["ok"] and g["scorer"]["excluded"] == {"missing_v": 1}  # p0107 is covered only through B
    # a mirror that misses p0107 passes the old (natural-boundary) check but fails the wrapper
    real = C.s14_reasons
    monkeypatch.setattr(C, "s14_reasons", lambda rs, st: {k: v for k, v in real(rs, st).items() if k != "p0107"})
    sub = tmp_path / "z"
    sub.mkdir()
    root2, reg2 = setup(sub, rows)
    with pytest.raises(R.Refused, match="parity"):
        RR(root2, reg2, "botA", six_bad_hours(), AS_OF)


def test_an_earlier_read_can_be_rerun_exit_after_as_of_is_open():
    rows = book(5, 5)
    p = next(r for r in rows if r["kind"] == "open")
    a = {x["attempt_id"]: x for x in C.attempts_from_rows(rows, DAY0, 0.25, as_of_ms=p["decision_ms"]["entry"] + 1000)}
    assert a["p0000"]["exit_ms"] is None and "p0001" not in a  # exit 5 s later than the cut, and a later decision is not yet an attempt


def test_unexpected_errors_exit_2(tmp_path, monkeypatch):
    root, reg = setup(tmp_path, book())
    hp = tmp_path / "h.jsonl"
    hp.write_text(health())
    monkeypatch.setattr(C, "attempts_from_rows", lambda *a, **k: 1 / 0)
    rp = tmp_path / "r.jsonl"
    rp.write_text("")
    assert R.main(["--ledger-root", root, "--registry", reg, "--bot", "botA", "--health", str(hp), "--health-sha", sha_of(health()), "--restarts", str(rp),
                   "--as-of-ms", str(AS_OF)]) == 2
    assert C.main(["--ledger-root", root, "--registry", reg, "--bot", "botA", "--as-of-ms", str(AS_OF), "--out", str(tmp_path / "o")]) == 2


# ---- QP R1 to R4
def test_r1_the_wrapper_records_the_one_read_and_a_second_is_refused(tmp_path):
    rows = book()
    root, reg = setup(tmp_path, rows)
    rd = tmp_path / "reads"
    code, lines, res = RR(root, reg, "botA", health(), AS_OF, record=True, reads_dir=str(rd))
    rec = json.loads((rd / "botA.json").read_text())
    assert rec["status"] == res["bots"]["botA"]["status"] and rec["attempts"] == 105
    am6 = rec["am6"]
    assert set(am6) == {"read_boundary_ms", "cut_sha256", "exclusions_sha256", "health_table_sha256", "health_merged_sha256", "restarts_sha256"}
    assert am6["restarts_sha256"] == hashlib.sha256(b"").hexdigest() and am6["read_boundary_ms"] == rec["read_boundary_ms"]
    with pytest.raises(S.Refused, match="already recorded"):
        RR(root, reg, "botA", health(), AS_OF, record=True, reads_dir=str(rd))
    # the CLI path records too (second CLI call is a refusal, exit 2)
    hp, rp = tmp_path / "h", tmp_path / "r"
    hp.write_text(health())
    rp.write_text("")
    args = ["--ledger-root", root, "--registry", reg, "--bot", "botA", "--health", str(hp), "--health-sha", sha_of(health()), "--restarts", str(rp), "--as-of-ms", str(AS_OF),
            "--record", "--reads-dir", str(rd)]
    assert R.main(args) == 2


def test_r1_a_not_decidable_verdict_is_recorded_as_final(tmp_path):
    bad = {DAY0 + d * DAY + (1 + k) * H: ["decision-input"] for d in (0, 1) for k in range(7)}
    root, reg = setup(tmp_path, book(130))
    rd = tmp_path / "reads"
    RR(root, reg, "botA", health(bad), AS_OF, record=True, reads_dir=str(rd))
    rec = json.loads((rd / "botA.json").read_text())
    assert rec["status"] == "NOT_DECIDABLE" and rec["reasons"] == ["NOT_DECIDABLE_10PCT"] and "am6" in rec


def test_r2_parity_runs_before_the_short_circuit_and_no_books_fails_closed(tmp_path, monkeypatch):
    bad = {DAY0 + d * DAY + (1 + k) * H: ["decision-input"] for d in (0, 1) for k in range(7)}
    root, reg = setup(tmp_path, [r if not (r["kind"] == "trade" and r["position_id"] == "p0100") else dict(r, v_src={"entry": "none", "exit": "row", "span": {"none": 1}})
                                  for r in book(130)])
    real = C.s14_reasons
    monkeypatch.setattr(C, "s14_reasons", lambda rs, st: {k: v for k, v in real(rs, st).items() if v != "missing_v"})
    with pytest.raises(R.Refused, match="parity"):  # the verdict would be NOT_DECIDABLE_10PCT, but a drifted mirror is refused first
        RR(root, reg, "botA", health(bad), AS_OF)
    monkeypatch.setattr(C, "s14_reasons", real)
    monkeypatch.setattr(S, "score_bot", lambda *a, **k: {"read_boundary_ms": None})
    assert C.parity_guard("botA", S.load_registry(reg), [], AS_OF, 0.25)["ok"] is False


def test_r3_notebooked_daily_tables_are_merged_and_must_match(tmp_path):
    root, reg = setup(tmp_path, book())
    full = health()
    lines = full.rstrip("\n").split("\n")[:-1]

    def table(ls):
        sha = hashlib.sha256("".join(x + "\n" for x in ls).encode()).hexdigest()
        return "\n".join(ls + [X.canon({"type": "sha256", "hours": len(ls), "sha256": sha})]) + "\n"
    t1, t2 = table(lines[:120]), table(lines[100:])  # overlap of 20 identical hours
    code, out, res = RR(root, reg, "botA", [t1, t2], AS_OF)
    assert res["bots"]["botA"]["attempts"] == 105 and any("health_tables" in x for x in out)
    with pytest.raises(R.Refused, match="expected"):
        RR(root, reg, "botA", [t1, t2], AS_OF, shas=[sha_of(t1)])  # missing expected sha
    with pytest.raises(R.Refused, match="expected"):
        RR(root, reg, "botA", [t1, t2], AS_OF, shas=[sha_of(t1), "f" * 64])
    flipped = list(lines[100:])
    flipped[0] = flipped[0].replace('"excluded":false', '"excluded":true')
    with pytest.raises(R.Refused, match="differs"):
        RR(root, reg, "botA", [t1, table(flipped)], AS_OF)
    with pytest.raises(SystemExit):  # --health-sha and --restarts are required on the command line
        R.main(["--bot", "botA", "--health", "x", "--as-of-ms", "1"])


def test_r4_restarts_required_and_unexplained_engine_starts_refused(tmp_path):
    rows = book()
    rows.append(dict(start_row("eng2"), decision_ms={"entry": DAY0 + 2 * DAY + 5 * H + 7, "exit": None}))  # a second engine start 2 days in
    root, reg = setup(tmp_path, rows)
    with pytest.raises(R.Refused, match="restart gap"):
        RR(root, reg, "botA", health(), AS_OF)
    gap = json.dumps([DAY0 + 2 * DAY + 5 * H - 600_000, DAY0 + 2 * DAY + 5 * H + 5000]) + "\n"
    code, out, res = RR(root, reg, "botA", health(), AS_OF, gap)
    assert any(f"restarts_sha256 {hashlib.sha256(gap.encode()).hexdigest()}" in x for x in out)
    with pytest.raises(R.Refused, match="--restarts is required"):
        R.run(root, reg, "botA", [health()], AS_OF, None, [sha_of(health())])
