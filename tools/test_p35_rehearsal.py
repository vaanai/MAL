"""tools/p35_rehearsal.py: rates, threshold rule, plan, guards. Synthetic tape only; no feed is started, no network. P35 PR 3."""
from __future__ import annotations

import json

import pytest

from tools import p35_rehearsal as R
from tools.test_feed_handover_proof import H0, MIN, continuous, write

T0, T1 = H0 + 10 * MIN, H0 + 50 * MIN  # 40 min = 20 windows of 120 s


def drop_every(rows, prog, n):
    """A tape that misses every n-th trade of `prog` (by second)."""
    out = []
    for r in rows:
        if r["type"] == "trade" and r["prog"] == prog and ((r["t_recv_ms"] - H0) // 1000) % n == 0:
            continue
        out.append(r)
    return out


def test_identical_tapes_have_zero_disagreement(tmp_path):
    a, b = write(tmp_path / "a", continuous()), write(tmp_path / "b", continuous())
    r = R.rates(a, b, T0, T1)
    ps = r["programs"]["pumpswap"]
    assert r["window"]["windows"] == 20
    assert ps["rows_a"] == ps["rows_b"] == 40 * 60 and ps["only_a"] == ps["only_b"] == 0
    assert ps["rate_max"] == 0 and ps["proposed_max_disagree_rate"] == 0.001  # the floor
    assert ps["rows_per_window_p05"] == 120 and ps["proposed_min_overlap_rows"] == 60


def test_disagreement_rate_and_rule(tmp_path):
    a = write(tmp_path / "a", continuous())
    b = write(tmp_path / "b", drop_every(continuous(), "pumpfun", 40))  # b misses 3 of every 120 pumpfun rows: 2.5%
    r = R.rates(a, b, T0, T1)
    pf, ps = r["programs"]["pumpfun"], r["programs"]["pumpswap"]
    assert pf["only_a"] == 60 and pf["only_b"] == 0
    assert pf["rate_max"] == pytest.approx(3 / 120)
    assert pf["proposed_max_disagree_rate"] == pytest.approx(2 * pf["rate_p99"])
    assert ps["only_a"] == 0
    mdr, mor = R.proposed(r)
    assert mdr["pumpfun"] > mdr["pumpswap"] and mor["pumpfun"] == int(0.5 * pf["rows_per_window_p05"])


def test_rates_output_is_counts_only(tmp_path):
    a, b = write(tmp_path / "a", continuous()), write(tmp_path / "b", continuous())
    text = json.dumps(R.rates(a, b, T0, T1))
    assert "sig-" not in text and "mint-" not in text


def test_proposed_refuses_empty_phase(tmp_path):
    a, b = write(tmp_path / "a", []), write(tmp_path / "b", [])
    (tmp_path / "a").mkdir(exist_ok=True)
    (tmp_path / "b").mkdir(exist_ok=True)
    with pytest.raises(R.Refused):
        R.proposed(R.rates(tmp_path / "a", tmp_path / "b", T0, T1))


def test_plan_instants():
    pl = R.plan(H0, 10_800, 21_600, 7_200, 1_200)
    assert pl["takeover1_ms"] - pl["phase1_end_ms"] == 1_200_000
    assert pl["takeover2_ms"] - pl["takeover1_ms"] == 7_200_000 and pl["standby2_ms"] == pl["takeover2_ms"] - 1_200_000
    assert pl["end_ms"] - pl["phase1_end_ms"] == 21_600_000
    with pytest.raises(R.Refused):
        R.plan(H0, 10_800, 3_600, 7_200, 1_200)


@pytest.mark.parametrize("root", ["/data/mal/arena/tape", "/data/mal/p35", "/data/mal"])
def test_root_under_data_mal_refused(root):
    with pytest.raises(R.Refused):
        R.check_root(root)


def test_research0_refused_before_hv6_end():
    with pytest.raises(R.Refused):
        R.check_host(R.EARLIEST_RESEARCH0_MS - 1, "mal-research-0")
    R.check_host(R.EARLIEST_RESEARCH0_MS, "mal-research-0")
    R.check_host(0, "mal-fast-0")


def test_earliest_matches_feed_v2_guard():
    from tools import arena_feed_v2

    assert R.EARLIEST_RESEARCH0_MS == arena_feed_v2.EARLIEST_PRODUCTION_MS


def test_cli_plan_only_and_refusals(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R.socket, "gethostname", lambda: "mal-fast-0")
    assert R.main(["run", "--root", str(tmp_path / "r"), "--plan-only"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"plan"} and not (tmp_path / "r").exists()  # plan-only creates nothing
    assert R.main(["run", "--root", "/data/mal/x", "--plan-only"]) == 2
    monkeypatch.setattr(R.socket, "gethostname", lambda: "mal-research-0")
    monkeypatch.setattr(R.time, "time_ns", lambda: (R.EARLIEST_RESEARCH0_MS - 1) * 1_000_000)
    assert R.main(["run", "--root", str(tmp_path / "r"), "--plan-only"]) == 2
