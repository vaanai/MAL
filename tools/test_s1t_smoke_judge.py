"""Tests of tools/s1t_smoke_judge.py (DEC-031 Amendment 24 items 6-7). Synthetic fixtures in tmp_path only: no tape, tip file, ledger or job output."""
from __future__ import annotations

import json
import re

import pytest

from tools import s1t_smoke_judge as j

T0 = 1_800_000_000_000  # an arbitrary synthetic instant
BT0 = T0 // 1000
AGES = (1200, 1500, 1800, 2000)  # digit strings avoid '37' and '53'
ALLOWED = [re.compile(p) for p in (
    r"S1T_SMOKE_(A_STARTED|B_DECISION_ROWS|C_NO_CRASH|D_MEMORY|DURATION|SEAL_HELD|AGE_BAR) (PASS|FAIL)",
    r"S1T_SMOKE_AGE_MS p50=(\d+|NA) p90=(\d+|NA) max=(\d+|NA)",
    r"S1T_SMOKE_CLASS_UNKNOWN (yes|no)",
    r"S1T_SMOKE_SIDE_FEED (reconnects=\d+ gaps=\d+|unavailable)",
    r"S1T_SMOKE_VERDICT (PASS|FAIL)")]


def L(rec: dict) -> str:
    return json.dumps(rec, separators=(",", ":"))


def trig(i: int, age: int | None = 1500, **kw) -> dict:
    rec = {"type": "trigger", "variant": "pv", "pool": f"P{i}", "block_time": BT0 + i, "t_detect_ms": (BT0 + i) * 1000 + (age or 0),
           "synthetic": False, "synthetic_src": "rpc", "h5_look2_sealed": True}
    if age is None:
        del rec["t_detect_ms"]
    rec.update(kw)
    return rec


def build(tmp_path, *, n_trig=37, n_pool=53, hours=25.0, ages=None, exit_code=0, log="INFO ok\n", rss=None, extra=(), start=True, tip_start=True,
          smoke=True, stats=True, stats_step_s=600, side=None, trig_kw=None):
    out, rep = tmp_path / "out", tmp_path / "rep"
    out.mkdir()
    rep.mkdir()
    lines = []
    if start:
        lines.append(L({"type": "start", "t_ms": T0}))
    if tip_start:
        lines.append(L({"type": "tip_input_start", "smoke": smoke, "t_ms": T0}))
    for i in range(n_trig):
        age = (ages[i] if ages else AGES[i % len(AGES)])
        lines.append(L(trig(i, age, **(trig_kw or {}))))
    lines += [L({"type": "pool", "pool": f"P{i}", "h5_look2_sealed": True, "min_q_pv_sol": None}) for i in range(n_pool)]
    if stats:
        k = 0
        while k * stats_step_s * 1000 <= hours * 3.6e6:
            lines.append(L({"type": "tip_input_stats", "t_ms": T0 + k * stats_step_s * 1000}))
            k += 1
    if side is not None:
        lines.append(L({"type": "hb", "t_ms": T0 + 5, "counters": {"x": 1}, "syn_class": side}))
    lines += list(extra)
    (out / "h5-shadow-2027-01-15T08.jsonl").write_text("\n".join(lines) + "\n")
    (out / "h5-shadow-status.json").write_text("{not json")  # a decoy: never read
    (rep / "process.log").write_text(log)
    rows = rss if rss is not None else [(1_000 + k * 600, 1_000_000_000 + k * 1000, 4242) for k in range(145)]
    (rep / "rss.tsv").write_text("".join(f"{t}\t{r}\t{p}\n" for t, r, p in rows))
    return out, rep, exit_code


def run(tmp_path, capsys, **kw):
    out, rep, code = build(tmp_path, **kw)
    rc = j.main(["--out-dir", str(out), "--rep-dir", str(rep), "--exit-code", str(code)])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def flag(text: str, name: str) -> str:
    m = re.search(rf"S1T_SMOKE_{name} (PASS|FAIL|yes|no)", text)
    assert m, text
    return m.group(1)


def test_all_pass_and_exit_zero(tmp_path, capsys):
    rc, out, err = run(tmp_path, capsys)
    assert rc == 0 and err == ""
    for name in j.FLAGS:
        assert flag(out, name) == "PASS", out
    assert "S1T_SMOKE_VERDICT PASS" in out
    assert "S1T_SMOKE_AGE_MS p50=1500 p90=2000 max=2000" in out


def test_stdout_has_no_trigger_or_pool_count(tmp_path, capsys):
    rc, out, _ = run(tmp_path, capsys, n_trig=37, n_pool=53, side={"ws_restarts": 5, "ws_feed_errors": 1})
    assert rc == 0
    assert "37" not in out and "53" not in out and "90" not in out.replace("p90=", "")  # distinctive counts never appear
    for ln in out.splitlines():
        assert any(p.fullmatch(ln) for p in ALLOWED), ln


@pytest.mark.parametrize("kw,name", [
    ({"start": False}, "A_STARTED"), ({"tip_start": False}, "A_STARTED"),
    ({"exit_code": 3}, "C_NO_CRASH"), ({"log": "x\nTraceback (most recent call last):\n"}, "C_NO_CRASH"),
    ({"hours": 23.0}, "DURATION"),
    ({"smoke": False}, "SEAL_HELD"), ({"trig_kw": {"h5_look2_sealed": False}}, "SEAL_HELD"),
    ({"extra": [L({"type": "tip_input_halted", "problem": "decoder_pin"})]}, "SEAL_HELD"),
    ({"extra": ['{"type":"trigger", broken']}, "SEAL_HELD"),
])
def test_each_flag_can_fail(tmp_path, capsys, kw, name):
    rc, out, _ = run(tmp_path, capsys, **kw)
    assert flag(out, name) == "FAIL" and "S1T_SMOKE_VERDICT FAIL" in out and rc == 1


def test_missing_process_log_fails_c(tmp_path, capsys):
    out, rep, _ = build(tmp_path)
    (rep / "process.log").unlink()
    assert j.main(["--out-dir", str(out), "--rep-dir", str(rep), "--exit-code", "0"]) == 1
    assert flag(capsys.readouterr().out, "C_NO_CRASH") == "FAIL"


def test_b_triggers_or_stats_through_the_run(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, n_trig=0)  # stats through the run carry B
    assert flag(out, "B_DECISION_ROWS") == "PASS" and flag(out, "AGE_BAR") == "FAIL" and "p50=NA" in out
    (tmp_path / "x").mkdir()
    _, out, _ = run(tmp_path / "x", capsys, n_trig=0, stats=False)
    assert flag(out, "B_DECISION_ROWS") == "FAIL" and flag(out, "DURATION") == "FAIL"
    (tmp_path / "y").mkdir()
    _, out, _ = run(tmp_path / "y", capsys, n_trig=0, stats_step_s=1800)  # stats too sparse to count as present through the run
    assert flag(out, "B_DECISION_ROWS") == "FAIL"
    (tmp_path / "z").mkdir()
    _, out, _ = run(tmp_path / "z", capsys, n_trig=1, stats=False)  # one trigger carries B alone
    assert flag(out, "B_DECISION_ROWS") == "PASS"


def test_d_memory_cases(tmp_path, capsys):
    grow = [(1_000 + k * 600, 1_000_000_000 + k * 600 * 100_000, 7) for k in range(145)]  # +8.6 GB over the run
    _, out, _ = run(tmp_path, capsys, rss=grow)
    assert flag(out, "D_MEMORY") == "FAIL"
    (tmp_path / "a").mkdir()
    _, out, _ = run(tmp_path / "a", capsys, rss=[(1, 1e9, 7)])  # one sample cannot fit a slope
    assert flag(out, "D_MEMORY") == "FAIL"
    (tmp_path / "b").mkdir()
    spike = [(1_000 + k * 600, 1_000_000_000 if k != 70 else 4_000_000_000, 7) for k in range(145)]  # a sample above the cap
    _, out, _ = run(tmp_path / "b", capsys, rss=spike)
    assert flag(out, "D_MEMORY") == "FAIL"
    (tmp_path / "c").mkdir()
    flat = [(1_000 + k * 600, 1_000_000_000, 7) for k in range(145)] + [(2_000, 9_000_000_000, 99)]  # a one-sample foreign pid is ignored
    _, out, _ = run(tmp_path / "c", capsys, rss=flat)
    assert flag(out, "D_MEMORY") == "PASS"
    (tmp_path / "d").mkdir()
    out_dir, rep, _ = build(tmp_path / "d")
    assert j.main(["--out-dir", str(out_dir), "--rep-dir", str(rep), "--exit-code", "0"]) == 0
    assert flag(capsys.readouterr().out, "D_MEMORY") == "PASS"
    assert j.main(["--out-dir", str(out_dir), "--rep-dir", str(rep), "--exit-code", "0", "--cap-mib", "500"]) == 1  # the cap is an argument
    assert flag(capsys.readouterr().out, "D_MEMORY") == "FAIL"


@pytest.mark.parametrize("line", [
    '{"type":"outcome","pool":"P1","pnl":1}', '{"type":"outcome_bx10","x":1}', '{"type":"strip","x":1}',
    '{"type":"outcome", this is not json', '{ "type" : "strip" garbage', '{"v":1,"type":"outcome_bx10"}'])
def test_outcome_class_line_fails_seal_even_if_not_json(tmp_path, capsys, line):
    rc, out, _ = run(tmp_path, capsys, extra=[line])
    assert flag(out, "SEAL_HELD") == "FAIL" and rc == 1


def test_outcome_lines_are_never_parsed(tmp_path, monkeypatch):
    out, rep, _ = build(tmp_path, extra=['{"type":"outcome","marker_zz_o":1}', '{"type":"outcome_bx10","marker_zz_b":1}', '{"type":"strip","marker_zz_s":1}'])
    seen = []
    real = json.loads

    def spy(s, *a, **k):
        seen.append(bytes(s))
        return real(s, *a, **k)

    monkeypatch.setattr(j.json, "loads", spy)
    j.judge(out, rep, 0)
    assert seen and not any(b"marker_zz" in s for s in seen)
    assert not any(b'"pool"' in s and b"min_q_pv_sol" in s for s in seen)  # pool records are not parsed either


def test_age_bar_share_and_unmeasurable(tmp_path, capsys):
    ok = [1000] * 39 + [4000]  # 1 of 40 over = 2.5%: at the bar
    _, out, _ = run(tmp_path, capsys, n_trig=40, ages=ok)
    assert flag(out, "AGE_BAR") == "PASS" and "max=4000" in out
    (tmp_path / "a").mkdir()
    _, out, _ = run(tmp_path / "a", capsys, n_trig=40, ages=[1000] * 38 + [4000, 4000])
    assert flag(out, "AGE_BAR") == "FAIL"
    (tmp_path / "b").mkdir()
    _, out, _ = run(tmp_path / "b", capsys, n_trig=40, ages=[1000] * 38 + [None, None])  # no t_detect_ms: counts as over the limit
    assert flag(out, "AGE_BAR") == "FAIL"
    (tmp_path / "c").mkdir()
    _, out, _ = run(tmp_path / "c", capsys, n_trig=10, ages=[3500] * 10)  # exactly the limit is not over it
    assert flag(out, "AGE_BAR") == "PASS"


def test_age_percentiles_nearest_rank(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, n_trig=10, ages=[100, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
    assert "S1T_SMOKE_AGE_MS p50=500 p90=900 max=1000" in out


def test_class_unknown_flag(tmp_path, capsys):
    cases = [({}, "no"), ({"trig_kw": {"synthetic_src": "unclassified"}}, "yes"), ({"trig_kw": {"synthetic": None}}, "yes"),
             ({"trig_kw": {"synthetic": True}}, "yes"), ({"extra": [L(trig(99, 1500, synthetic_src=None))]}, "yes"),
             ({"extra": [L({k: v for k, v in trig(98).items() if k != "synthetic"})]}, "yes")]
    for n, (kw, want) in enumerate(cases):
        d = tmp_path / str(n)
        d.mkdir()
        _, out, _ = run(d, capsys, **kw)
        assert flag(out, "CLASS_UNKNOWN") == want, kw
        if want == "yes" and not kw.get("trig_kw"):
            assert flag(out, "AGE_BAR") == "PASS"  # information only: not in the verdict


def test_side_feed_counters(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, side={"ws_restarts": 4, "ws_feed_errors": 2, "ws_decode_errors": 9})
    assert "S1T_SMOKE_SIDE_FEED reconnects=4 gaps=2" in out
    (tmp_path / "a").mkdir()
    _, out, _ = run(tmp_path / "a", capsys, side={"ws_restarts": 6}, extra=[L({"type": "stop", "t_ms": T0 + 9, "syn_class": {"ws_restarts": 8, "ws_feed_errors": 3}})])
    assert "S1T_SMOKE_SIDE_FEED reconnects=8 gaps=3" in out  # the last record carrying syn_class
    (tmp_path / "b").mkdir()
    _, out, _ = run(tmp_path / "b", capsys)
    assert "S1T_SMOKE_SIDE_FEED unavailable" in out


def test_usage_errors_exit_2(tmp_path, capsys):
    assert j.main(["--out-dir", str(tmp_path / "nope"), "--rep-dir", str(tmp_path), "--exit-code", "0"]) == 2
    with pytest.raises(SystemExit) as e:
        j.main(["--out-dir", str(tmp_path), "--rep-dir", str(tmp_path)])  # --exit-code missing
    assert e.value.code == 2
