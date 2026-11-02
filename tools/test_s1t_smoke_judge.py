"""Tests of tools/s1t_smoke_judge.py (DEC-031 Amendment 24 items 6-7, QP-NOTE-1102-tip-readers). Synthetic fixtures in tmp_path only, shaped like S*' records
(tools/h5_shadow.py and tools/h5_shadow_tip.py at afa4971d): no tape, tip file, ledger or job output."""
from __future__ import annotations

import json
import re

import pytest

from tools import s1t_smoke_judge as j

T0 = 1_800_000_000_000  # an arbitrary synthetic instant
BT0 = T0 // 1000
AGES = (1200, 1500, 1800, 2000)  # digit strings avoid the distinctive counts below
WS = j.WS_ENDPOINT
ALLOWED = [re.compile(p) for p in (
    r"S1T_SMOKE_(A_STARTED|B_DECISION_ROWS|C_NO_CRASH|D_MEMORY|DURATION|SEAL_HELD|AGE_BAR) (PASS|FAIL)",
    r"S1T_SMOKE_AGE_MS p50=(\d+|NA) p90=(\d+|NA) max=(\d+|NA)",
    r"S1T_SMOKE_CLASS_UNKNOWN (yes|no)",
    r"S1T_SMOKE_SIDE_FEED (reconnects=\d+ feed_errors=\d+ decode_errors=\d+|unavailable)",
    r"S1T_SMOKE_REPORT dropped_rows (unavailable|([a-z_]+=\d+ ?)+)",
    r"S1T_SMOKE_REPORT rows_per_min=(\d+\.\d|unavailable)",
    r"S1T_SMOKE_REPORT rss_peak_mib=(\d+\.\d|unavailable)",
    r"S1T_SMOKE_VERDICT (PASS|FAIL)")]


def L(rec: dict) -> str:
    return json.dumps(rec, separators=(",", ":"))


def start_rec(**over) -> dict:
    """S*'s start record in fresh mode (the fields the pins read, in S*'s nesting)."""
    rec = {"type": "start", "rule": "H5-BOOSTFLOOR-Q50 v1", "code_sha": j.CODE_SHA, "argv": ["--fresh-forward", "ARENA", "--q-star-sol", "50"], "pid": 4242,
           "h5_look2": {"reason": "x", "start_ms": None, "applied": False, "observed": False, "amendment_ref": None, "end_only_ms": 1796083200000,
                        "end_only": "2026-12-01T00:00:00Z"},
           "q_star_sol": 50.0, "fresh_forward": "ARENA", "tip_union": "off", "dirty": False, "endpoints": [WS], "host": "synthetic-host",
           "v": 1, "schema": "s", "t_ms": T0}
    rec.update(over)
    return rec


def trig(i: int, age: int | None = 1500, **kw) -> dict:
    """S*'s trigger record: `synthetic` is hard-coded False and `synthetic_src` is always set (tools/h5_shadow.py _fire)."""
    rec = {"type": "trigger", "variant": "pv", "pool": f"P{i}", "block_time": BT0 + i, "t_detect_ms": (BT0 + i) * 1000 + (age or 0),
           "synthetic": False, "synthetic_src": "rpc", "h5_look2_sealed": True, "v": 1, "t_ms": T0}
    if age is None:
        del rec["t_detect_ms"]
    rec.update(kw)
    return rec


def excluded(reason: str = "unclassified", i: int = 5) -> dict:
    """S*'s `excluded` record (tools/h5_shadow.py _exclude)."""
    return {"type": "excluded", "reason": reason, "pool": f"P{i}", "mint": f"M{i}", "s0": 1000 + i, "slot": 1010 + i, "v": 1, "schema": "s", "t_ms": T0}


def stats(k: int, step_s: int) -> dict:
    return {"type": "tip_input_stats", "schema": "s", "smoke_outcome_blocked": 0, "t_ms": T0 + k * step_s * 1000, "problem": None, "halted": False,
            "driver": {"rows_trade_pumpswap": 777, "rows_pool_create": 59, "rows_bad": 2, "rows_bad_trade_fields": 2},
            "source": {"polls": 5, "rows": k * 100, "reader_loss_bad_lines": 1, "tap_bad_lines": 1}}


def build(tmp_path, *, n_trig=37, n_pool=59, hours=25.0, ages=None, exit_code=0, log="INFO ok\n", rss=None, extra=(), start=None, tip_start=True,
          smoke=True, with_stats=True, stats_step_s=600, side=None, trig_kw=None):
    out, rep = tmp_path / "out", tmp_path / "rep"
    out.mkdir()
    rep.mkdir()
    lines = [L(start_rec() if start is None else start)] if start is not False else []
    if tip_start:
        lines.append(L({"type": "tip_input_start", "smoke": smoke, "t_ms": T0}))
    for i in range(n_trig):
        lines.append(L(trig(i, (ages[i] if ages else AGES[i % len(AGES)]), **(trig_kw or {}))))
    lines += [L({"type": "pool", "pool": f"P{i}", "h5_look2_sealed": True, "min_q_pv_sol": None}) for i in range(n_pool)]
    if with_stats:
        k = 0
        while k * stats_step_s * 1000 <= hours * 3.6e6:
            lines.append(L(stats(k, stats_step_s)))
            k += 1
    if side is not None:
        lines.append(L({"type": "hb", "t_ms": T0 + 5, "counters": {"x": 1}, "syn_class": side}))
    lines += list(extra)
    (out / "h5-shadow-2027-01-15T08.jsonl").write_text("\n".join(lines) + "\n")
    (out / "h5-shadow-status.json").write_text("{not json")  # a decoy: never read
    (out / "h5-shadow-errors.log").write_text("Traceback (decoy: never read)\n")
    (rep / "process.log").write_text(log)
    rows = rss if rss is not None else [(1_000 + k * 600, 1_000_000_000 + k * 1000, 4242) for k in range(145)]
    (rep / "rss.tsv").write_text("".join(f"{t}\t{r}\t{p}\n" for t, r, p in rows))
    return out, rep, exit_code


def argv(out, rep, code, **over):
    a = {"--out-dir": out, "--rep-dir": rep, "--exit-code": code, "--ws-endpoint": WS, "--plan-hours": 578, "--cap-mib": 3500}
    a.update(over)
    return [x for k, v in a.items() for x in (k, str(v))]


def run(tmp_path, capsys, **kw):
    out, rep, code = build(tmp_path, **kw)
    rc = j.main(argv(out, rep, code))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def flag(text: str, name: str) -> str:
    m = re.search(rf"S1T_SMOKE_{name} (PASS|FAIL|yes|no)", text)
    assert m, text
    return m.group(1)


def sub(d, name):
    p = d / name
    p.mkdir()
    return p


def test_all_pass_and_exit_zero(tmp_path, capsys):
    rc, out, err = run(tmp_path, capsys)
    assert rc == 0 and err == ""
    for name in j.FLAGS:
        assert flag(out, name) == "PASS", out
    assert "S1T_SMOKE_VERDICT PASS" in out and "S1T_SMOKE_CLASS_UNKNOWN no" in out
    assert "S1T_SMOKE_AGE_MS p50=1500 p90=2000 max=2000" in out


def test_stdout_has_no_trigger_or_pool_count(tmp_path, capsys):
    rc, out, _ = run(tmp_path, capsys, n_trig=37, n_pool=59, side={"ws_restarts": 5, "ws_feed_errors": 1, "ws_decode_errors": 0})
    assert rc == 0
    for banned in ("37", "59", "777"):  # trigger count, pool count (also the driver's rows_pool_create decoy), the driver's per-type trade rows
        assert banned not in out, banned
    for ln in out.splitlines():
        assert any(p.fullmatch(ln) for p in ALLOWED), ln


def test_report_lines(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys)
    assert "S1T_SMOKE_REPORT dropped_rows apply_errors=0 bad_lines=1 pool_create_fields=0 trade_fields=2 trade_venue=0 zst_errors=0" in out
    assert "S1T_SMOKE_REPORT rows_per_min=10.0" in out  # source.rows 15000 over 1500 min
    assert f"S1T_SMOKE_REPORT rss_peak_mib={(1_000_000_000 + 144 * 1000) / j.MIB:.1f}" in out
    d = sub(tmp_path, "n")
    _, out, _ = run(d, capsys, with_stats=False, rss=[])
    assert "dropped_rows unavailable" in out and "rows_per_min=unavailable" in out and "rss_peak_mib=unavailable" in out


@pytest.mark.parametrize("mut,name", [
    (lambda r: r.update(code_sha="b" * 40), "code_sha"), (lambda r: r.update(dirty=True), "dirty"), (lambda r: r.update(fresh_forward="NO"), "fresh_forward"),
    (lambda r: r.update(q_star_sol=40.0), "q_star_sol"), (lambda r: r["h5_look2"].update(end_only_ms=1796083200001), "end_only_ms"),
    (lambda r: r.update(endpoints=["wss://other.example.org/x"]), "endpoints"), (lambda r: r.update(endpoints=[WS, "wss://other.example.org/x"]), "endpoints2"),
    (lambda r: r.pop("dirty"), "no dirty")])
def test_each_start_pin_mismatch_fails_a(tmp_path, capsys, mut, name):
    rec = start_rec()
    mut(rec)
    rc, out, _ = run(tmp_path, capsys, start=rec)
    assert flag(out, "A_STARTED") == "FAIL" and rc == 1, name


def test_a_needs_a_start_and_a_tip_start(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, start=False)
    assert flag(out, "A_STARTED") == "FAIL"
    _, out, _ = run(sub(tmp_path, "a"), capsys, tip_start=False)
    assert flag(out, "A_STARTED") == "FAIL"


def test_ws_endpoint_must_equal_the_pin(tmp_path, capsys):
    out, rep, code = build(tmp_path)
    assert j.main(argv(out, rep, code, **{"--ws-endpoint": "wss://rpc.other.example/public"})) == 2
    assert "pinned" in capsys.readouterr().err
    # and the judge compares the start record's endpoints with the argument it is given
    assert j.judge(out, rep, 0, 3500, 578, ws_endpoint="wss://rpc.other.example/public")["flags"]["A_STARTED"] is False
    assert j.judge(out, rep, 0, 3500, 578)["flags"]["A_STARTED"] is True


def test_required_arguments_have_no_default(tmp_path):
    out, rep, code = build(tmp_path)
    for missing in ("--plan-hours", "--cap-mib", "--ws-endpoint", "--exit-code"):
        a = argv(out, rep, code)
        i = a.index(missing)
        del a[i:i + 2]
        with pytest.raises(SystemExit) as e:
            j.main(a)
        assert e.value.code == 2, missing


@pytest.mark.parametrize("kw,name", [
    ({"exit_code": 3}, "exit"), ({"log": "x\nTraceback (most recent call last):\n"}, "traceback"),
    ({"extra": [L(start_rec(t_ms=T0 + 9))]}, "second start"),
    ({"extra": [L({"type": "tip_input_start", "smoke": True, "t_ms": T0 + 9})]}, "second tip_input_start"),
    ({"rss": [(1_000 + k * 600, 1_000_000_000, 4242 if k < 70 else 4243) for k in range(145)]}, "pid change"),
    ({"rss": [(1_000 + k * 600, 1_000_000_000, "4242" if k != 70 else "-") for k in range(145)]}, "process gone")])
def test_c_no_crash_fails_on_a_crash_or_a_restart(tmp_path, capsys, kw, name):
    rc, out, _ = run(tmp_path, capsys, **kw)
    assert flag(out, "C_NO_CRASH") == "FAIL" and rc == 1, name
    assert flag(out, "A_STARTED") == ("PASS" if name != "second start" else "PASS")  # the restart is C's finding; a second pinned start still pins


def test_missing_process_log_fails_c(tmp_path, capsys):
    out, rep, _ = build(tmp_path)
    (rep / "process.log").unlink()
    assert j.main(argv(out, rep, 0)) == 1
    assert flag(capsys.readouterr().out, "C_NO_CRASH") == "FAIL"


def test_the_errors_log_and_status_file_are_not_read(tmp_path, capsys):
    out, rep, _ = build(tmp_path)  # the decoys hold 'Traceback' and invalid JSON
    assert j.main(argv(out, rep, 0)) == 0


def test_b_triggers_or_stats_through_the_run(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, n_trig=0)  # stats through the run carry B
    assert flag(out, "B_DECISION_ROWS") == "PASS" and flag(out, "AGE_BAR") == "FAIL" and "p50=NA" in out
    _, out, _ = run(sub(tmp_path, "x"), capsys, n_trig=0, with_stats=False)
    assert flag(out, "B_DECISION_ROWS") == "FAIL" and flag(out, "DURATION") == "FAIL"
    _, out, _ = run(sub(tmp_path, "y"), capsys, n_trig=0, stats_step_s=1800)  # stats too sparse to count as present through the run
    assert flag(out, "B_DECISION_ROWS") == "FAIL"
    _, out, _ = run(sub(tmp_path, "z"), capsys, n_trig=1, with_stats=False)  # one trigger carries B alone
    assert flag(out, "B_DECISION_ROWS") == "PASS"


def test_duration(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, hours=23.0)
    assert flag(out, "DURATION") == "FAIL"


def rss_rows(f, n=145, pid=7):
    return [(1_000 + k * 600, f(k * 600), pid) for k in range(n)]


def test_d_memory_cases(tmp_path, capsys):
    # a start-up ramp in the first 30 min is outside the fit; flat afterwards
    ramp = rss_rows(lambda s: 500_000_000 + min(s, 1799) * 1_000_000)
    _, out, _ = run(tmp_path, capsys, rss=ramp)
    assert flag(out, "D_MEMORY") == "PASS"
    # a steady rise fails over the 578 h window but not over a 24 h one
    rise = rss_rows(lambda s: 1_000_000_000 + s * 1500)
    out_dir, rep, _ = build(sub(tmp_path, "a"), rss=rise)
    assert j.main(argv(out_dir, rep, 0)) == 1 and flag(capsys.readouterr().out, "D_MEMORY") == "FAIL"
    assert j.main(argv(out_dir, rep, 0, **{"--plan-hours": 24})) == 0 and flag(capsys.readouterr().out, "D_MEMORY") == "PASS"
    assert j.main(argv(out_dir, rep, 0, **{"--cap-mib": 500, "--plan-hours": 24})) == 1  # the cap is an argument
    # too few samples after the warm-up, and a metric that cannot be computed, fail
    _, out, _ = run(sub(tmp_path, "b"), capsys, rss=rss_rows(lambda s: 1e9, n=6))
    assert flag(out, "D_MEMORY") == "FAIL"
    _, out, _ = run(sub(tmp_path, "c"), capsys, rss=[])
    assert flag(out, "D_MEMORY") == "FAIL"
    # a sample above the cap fails even when the fit is flat
    _, out, _ = run(sub(tmp_path, "d"), capsys, rss=rss_rows(lambda s: 4_000_000_000 if s == 600 * 70 else 1e9))
    assert flag(out, "D_MEMORY") == "FAIL"


@pytest.mark.parametrize("kw,name", [
    ({"trig_kw": {"h5_look2_sealed": False}}, "unsealed trigger"), ({"smoke": False}, "smoke false"),
    ({"extra": [L({"type": "tip_input_halted", "problem": "decoder_pin"})]}, "halted"),
    ({"extra": ['{"type":"trigger", broken']}, "unparseable trigger")])
def test_seal_held_can_fail(tmp_path, capsys, kw, name):
    rc, out, _ = run(tmp_path, capsys, **kw)
    assert flag(out, "SEAL_HELD") == "FAIL" and rc == 1, name


@pytest.mark.parametrize("line", [
    '{"type":"outcome","pool":"P1","pnl":1}', '{"type":"outcome_bx10","x":1}', '{"type":"strip","x":1}',
    '{"type":"outcome", this is not json', '{ "type" : "strip" garbage', '{"v":1,"type":"outcome_bx10"}'])
def test_outcome_class_line_fails_seal_even_if_not_json(tmp_path, capsys, line):
    rc, out, _ = run(tmp_path, capsys, extra=[line])
    assert flag(out, "SEAL_HELD") == "FAIL" and rc == 1


def test_outcome_and_excluded_lines_are_never_parsed(tmp_path, monkeypatch):
    out, rep, _ = build(tmp_path, extra=['{"type":"outcome","marker_zz_o":1}', '{"type":"outcome_bx10","marker_zz_b":1}', '{"type":"strip","marker_zz_s":1}',
                                         L({**excluded(), "marker_zz_x": 1}), L(start_rec(marker_zz_start=1))])
    seen = []
    real = json.loads

    def spy(s, *a, **k):
        seen.append(bytes(s))
        return real(s, *a, **k)

    monkeypatch.setattr(j.json, "loads", spy)
    res = j.judge(out, rep, 0, 3500, 578)
    assert seen and not any(b"marker_zz" in s for s in seen)  # outcome, excluded and start lines are byte-tested only
    assert not any(b"min_q_pv_sol" in s for s in seen)  # pool records are not parsed either
    assert res["class_unknown"] is True


def test_class_unknown_from_a_real_excluded_record(tmp_path, capsys):
    """The reviewer's case (M1): S* writes no trigger for an unclassified pool, only {"type":"excluded","reason":"unclassified",...}."""
    _, out, _ = run(tmp_path, capsys, extra=[L(excluded("unclassified"))])
    assert flag(out, "CLASS_UNKNOWN") == "yes"
    assert flag(out, "AGE_BAR") == "PASS" and "S1T_SMOKE_VERDICT PASS" in out  # information only: not in the verdict
    _, out, _ = run(sub(tmp_path, "a"), capsys, extra=[L(excluded("synthetic"))])  # a KNOWN synthetic class is not an unknown one
    assert flag(out, "CLASS_UNKNOWN") == "no"
    _, out, _ = run(sub(tmp_path, "b"), capsys)
    assert flag(out, "CLASS_UNKNOWN") == "no"
    _, out, _ = run(sub(tmp_path, "c"), capsys, extra=['{"type":"excluded","reason":"unclassified", not json'])  # a byte test: no parse needed
    assert flag(out, "CLASS_UNKNOWN") == "yes"


def test_age_bar_share_and_unmeasurable(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, n_trig=40, ages=[1000] * 39 + [4000])  # 1 of 40 over = 2.5%: at the bar
    assert flag(out, "AGE_BAR") == "PASS" and "max=4000" in out
    _, out, _ = run(sub(tmp_path, "a"), capsys, n_trig=40, ages=[1000] * 38 + [4000, 4000])
    assert flag(out, "AGE_BAR") == "FAIL"
    _, out, _ = run(sub(tmp_path, "b"), capsys, n_trig=40, ages=[1000] * 38 + [None, None])  # no t_detect_ms: counts as over the limit
    assert flag(out, "AGE_BAR") == "FAIL"
    _, out, _ = run(sub(tmp_path, "c"), capsys, n_trig=10, ages=[3500] * 10)  # exactly the limit is not over it
    assert flag(out, "AGE_BAR") == "PASS"


def test_age_percentiles_nearest_rank(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, n_trig=10, ages=[100, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
    assert "S1T_SMOKE_AGE_MS p50=500 p90=900 max=1000" in out


def test_side_feed_counters(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, side={"ws_restarts": 4, "ws_feed_errors": 2, "ws_decode_errors": 9})
    assert "S1T_SMOKE_SIDE_FEED reconnects=4 feed_errors=2 decode_errors=9" in out
    _, out, _ = run(sub(tmp_path, "a"), capsys, side={"ws_restarts": 6},
                    extra=[L({"type": "stop", "t_ms": T0 + 9, "syn_class": {"ws_restarts": 8, "ws_feed_errors": 3, "ws_decode_errors": 1}})])
    assert "S1T_SMOKE_SIDE_FEED reconnects=8 feed_errors=3 decode_errors=1" in out  # the last record carrying syn_class
    _, out, _ = run(sub(tmp_path, "b"), capsys)
    assert "S1T_SMOKE_SIDE_FEED unavailable" in out


def test_usage_errors_exit_2(tmp_path):
    out, rep, code = build(tmp_path)
    assert j.main(argv(tmp_path / "nope", rep, code)) == 2
    assert j.main(argv(out, tmp_path / "nope", code)) == 2
