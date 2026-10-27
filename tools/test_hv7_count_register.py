"""HV7 C3a tests: tools/hv7_count.py and tools/hv7_register.py. Synthetic rows and registries only; the real bots.json and decision logs are never opened."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from tools import arena_paper
from tools import hv7_count as K
from tools import hv7_register as G
from tools import hv7_runner as R
from tools.arena_declog import DecisionLog
from tools.test_hv7_resume import mod_env  # noqa: F401
from tools.test_hv7_runner import _env  # noqa: F401

DAY1, DAY2 = 1_790_000_000_000, 1_790_000_000_000 + 86_400_000


def fill(root: Path):
    log = R.Hv7DecisionLog(root)
    for ms in (DAY1, DAY1 + 3_600_000, DAY2):
        for arm in R.ARM_ORDER:
            log.append(arm, ms, {"kind": "entry", "order_id": f"{arm}{ms}", "pool": "P", "est_ret": 0.5})
    # L rank rows: 1 ok, 2 fallback, 1 excluded (restart)
    log.append("L", DAY1, {"kind": "rank", "fallback": False})
    log.append("L", DAY1 + 3_600_000, {"kind": "rank", "fallback": True})
    log.append("L", DAY2, {"kind": "rank", "fallback": True})
    log.append("L", DAY2 + 1, {"kind": "rank", "fallback": True, "excluded": True})
    log.append("R", DAY2, {"kind": "sell", "pid": "x", "est_ret": -9.0})
    return log


def test_count_attempts_fallbacks_excluded_and_days(tmp_path, mod_env):
    fill(tmp_path / "v7")
    c = K.count(tmp_path / "v7")
    assert c["attempts"] == {"L": 3, "R": 3, "S": 3, "U": 3}
    assert (c["rank_rows"], c["fallbacks"], c["excluded_periods"]) == (3, 2, 1)
    assert c["fallback_share"] == pytest.approx(2 / 3) and c["fallback_share_gt_10pct"] is True
    assert c["nonzero_days"] == {"any": 2, "L": 2, "R": 2, "S": 2, "U": 2}
    assert c["torn_lines"] == 0
    assert K.count(tmp_path / "v7", clock_start_ms=DAY2)["attempts"]["L"] == 1


def test_count_ignores_the_late_reply_counter_and_reads_no_outcome_field(tmp_path, mod_env):
    fill(tmp_path / "v7")
    src = Path(K.__file__).read_text()
    code = src.split('"""', 2)[2]
    assert "late_reply" not in code and "est_ret" not in code and "counters" not in code and "pnl" not in code.lower()
    out = io.StringIO()
    assert K.main(["--root", str(tmp_path / "v7")], out=out) == 0
    printed = json.loads(out.getvalue())
    assert set(printed) == {"attempts", "rank_rows", "fallbacks", "excluded_periods", "fallback_share", "fallback_share_gt_10pct", "nonzero_days", "torn_lines", "clock_start_ms"}


def test_count_counts_a_torn_line_and_does_not_raise(tmp_path, mod_env):
    log = fill(tmp_path / "v7")
    f = log.files("U")[-1]
    with open(f, "ab") as fh:
        fh.write(b'{"kind":"ent')
    assert K.count(tmp_path / "v7")["torn_lines"] == 1


def test_count_empty_root_is_all_zero_and_no_share(tmp_path, mod_env):
    c = K.count(tmp_path / "none")
    assert c["attempts"] == {a: 0 for a in R.ARM_ORDER} and c["fallback_share"] is None and c["fallback_share_gt_10pct"] is None


# ------------------------------------------------------------------------------------------------------------------------ register
NOW, CLOCK, HALT, SHA = 1_000_000, 2_000_000, 9_000_000, "a" * 40


def run_reg(argv, reg):
    out, err = io.StringIO(), io.StringIO()
    rc = G.main(["--sha", SHA, "--clock-start-ms", str(CLOCK), "--entries-halt-ms", str(HALT), *argv], out=out, err=err, _now_ms=NOW, _verify_sha=False)
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.setenv(arena_paper.TEST_ENV, "1")


def test_bot_ids_and_models_are_pinned():
    assert G.HV7_BOTS == ("hv7.0-L", "hv7.0-R", "hv7.0-S", "hv7.0-U")
    assert G.HV7_MODELS["hv7.0-L"] and all(G.HV7_MODELS[b] == [] for b in G.HV7_BOTS[1:])


def test_dry_run_is_the_default_and_writes_nothing(tmp_path):
    reg = tmp_path / "bots.json"
    rc, out, err = run_reg(["--registry", str(reg)], reg)
    assert rc == 0 and "DRY RUN" in out and not reg.exists()
    for pin in (SHA, str(CLOCK), str(HALT), "hv7.0-U", "0.3", "3.0", G.confirm_phrase(SHA, CLOCK, HALT)):
        assert pin in out


def test_execute_registers_four_books_and_rerun_is_a_noop(tmp_path):
    reg = tmp_path / "bots.json"
    rc, out, err = run_reg(["--registry", str(reg), "--execute"], reg)
    assert rc == 0 and sorted(arena_paper.load_registry(reg)) == list(G.HV7_BOTS)
    before = reg.read_bytes()
    assert run_reg(["--registry", str(reg), "--execute"], reg)[0] == 0 and reg.read_bytes() == before


def test_live_registry_needs_the_exact_confirm_and_the_arena_path_is_refused_without_it(tmp_path, monkeypatch):
    rc, out, err = run_reg(["--live-registry", "--execute"], None)
    assert rc == 2 and err.strip() == "Refused"
    rc, out, err = run_reg(["--live-registry", "--execute", "--confirm", G.confirm_phrase(SHA, CLOCK, HALT + 1)], None)
    assert rc == 2                                           # a phrase for other pins does not work
    assert run_reg(["--registry", "/data/mal/arena/bots.json", "--execute"], None)[0] == 2
    assert run_reg([], None)[0] == 2                          # no registry and no --live-registry
    monkeypatch.setattr(arena_paper, "REGISTRY_PATH", str(tmp_path / "live.json"))
    monkeypatch.setattr(G.arena_paper, "REGISTRY_PATH", str(tmp_path / "live.json"))
    rc, *_ = run_reg(["--live-registry", "--execute", "--confirm", G.confirm_phrase(SHA, CLOCK, HALT)], None)
    assert rc == 0 and sorted(arena_paper.load_registry(tmp_path / "live.json")) == list(G.HV7_BOTS)


@pytest.mark.parametrize("bad", [["--sha", "xyz"], ["--clock-start-ms", "500"], ["--entries-halt-ms", "2000000"]])
def test_bad_pins_are_refused(tmp_path, bad):
    argv = ["--sha", SHA, "--clock-start-ms", str(CLOCK), "--entries-halt-ms", str(HALT), "--registry", str(tmp_path / "r.json"), "--execute"]
    for i in range(0, len(bad), 2):
        argv[argv.index(bad[i]) + 1] = bad[i + 1]
    out, err = io.StringIO(), io.StringIO()
    assert G.main(argv, out=out, err=err, _now_ms=NOW, _verify_sha=False) == 2 and not (tmp_path / "r.json").exists()


def test_a_changed_value_for_a_registered_id_is_refused_and_masked(tmp_path):
    reg = tmp_path / "bots.json"
    assert run_reg(["--registry", str(reg), "--execute"], reg)[0] == 0
    out, err = io.StringIO(), io.StringIO()
    rc = G.main(["--sha", SHA, "--clock-start-ms", str(CLOCK + 1), "--entries-halt-ms", str(HALT), "--registry", str(reg), "--execute"], out=out, err=err, _now_ms=NOW, _verify_sha=False)
    assert rc == 1 and err.getvalue().strip() == "ArenaRefused hv7.0-L"


def test_register_all_refuses_a_foreign_id(tmp_path):
    with pytest.raises(G.Refused):
        G.register_all(SHA, CLOCK, tmp_path / "r.json", bot_ids=("hv6.0-L",), now_ms=NOW, verify_sha=False)
