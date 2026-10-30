"""drill_hv6_real_layout on a SYNTHETIC tape dir that mimics the real hour-file layout (tape-<YYYY-MM-DDTHH>.jsonl and .jsonl.zst, as tools/arena_feed writes them).
No real tape, no API, no RPC. Nothing here says anything about profit."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import drill_hv6_item6 as D
from tools import drill_hv6_real_layout as RL
from tools import hv6_cards as H

MIN = 60_000
HOUR = 3_600_000


def shifted_rows(day: str) -> list[dict]:
    delta = RL.day_ms(day) + 6 * HOUR - D.T0  # the fixture's T0 is an hour boundary; keep it on one
    rows = D.trade_rows()
    for r in rows:
        r["t_recv_ms"] += delta
        if "event_ts" in r:
            r["event_ts"] += delta // 1000
    return rows


def write_layout(d: Path, rows: list[dict], zst_hours: int = 2) -> None:
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(D._hour_name(r["t_recv_ms"]), []).append(json.dumps(r))
    for i, (name, lines) in enumerate(sorted(by.items())):
        f = d / name
        f.write_text("\n".join(lines) + "\n")
        if i < zst_hours and shutil.which("zstd"):
            subprocess.run(["zstd", "-q", "--rm", str(f)], check=True)  # closed hours are compacted to .jsonl.zst, as arena_feed does


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    real = str(tmp_path.resolve())
    monkeypatch.setattr(H, "READ_ROOTS", (real,))
    monkeypatch.setattr(H, "WRITE_ROOTS", (real,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    tape = tmp_path / "tape"
    monkeypatch.setattr(RL, "REAL_TAPE", str(tape))
    return tape


def test_day_list_refusals():
    assert RL.parse_days("2026-10-21,2026-10-22") == ["2026-10-21", "2026-10-22"]
    for bad in ("", "2026-10-19", "2026-10-25", "2026-10-24,2026-10-25", "2026-10-21,2026-10-23", "2026-10-22,2026-10-21", "2026-10-21,2026-10-21", "10-21", "2026-10-1x",
                "2026-09-25", "2026-10-21; rm"):
        with pytest.raises(RL.Refused):
            RL.parse_days(bad)


def test_hour_scan_and_filter_and_refusals(tmp_path, _sandbox):
    tape = _sandbox
    write_layout(tape, shifted_rows("2026-10-21"))
    for junk in ("tape-2026-10-19T23.jsonl", "tape-2026-10-25T00.jsonl", "tape-2026-10-25T00.jsonl.zst", "tape-2026-10-22T00.jsonl", "notes.txt"):
        (tape / junk).write_text("{}\n")
    info = RL.hour_scan(tape, ["2026-10-21"])
    assert info["hours_expected"] == 24 and info["hours_present"] == 5 and info["hours_missing"] == 19
    base = tmp_path / "drill"
    base.mkdir()
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    rd = RL.RealDrill(base, tape, "a" * 40, ["2026-10-21"])
    keys = [k for k, _, _ in rd.new_run("f").state._hour_files()]
    assert keys and all(k.startswith("2026-10-21T") for k in keys)  # 10-19, 10-22 and 10-25 hours are never listed
    with pytest.raises(RL.Refused):
        RL.run_real(["2026-10-21"], "x", "a" * 40, tape_dir=str(tmp_path / "other"), base=base)
    with pytest.raises(RL.Refused):
        RL.run_real(["2026-10-21"], "../x", "a" * 40, base=base)


def test_two_runs_equal_md5_prints_counts_only(tmp_path, _sandbox, capsys):
    tape = _sandbox
    rows = shifted_rows("2026-10-21")
    write_layout(tape, rows)
    base = tmp_path / "drill" / "t1"
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    t0 = RL.day_ms("2026-10-21") + 6 * HOUR
    res = RL.run_real(["2026-10-21"], "t1", "a" * 40, base=base, _window=(t0 + 9 * MIN + 2000, t0 + 70 * MIN))
    out = capsys.readouterr().out
    assert res["result"] == "PASS" and res["md5_equal"] and res["run1_log_md5"] == res["run2_log_md5"]
    assert res["run1"]["cards"] > 0 and sum(res["run1"]["entries"].values()) > 0 and res["run1"]["scans"] > 0 and res["peak_rss_mb"] > 0
    assert res["run1"]["scan_ms_p95"] >= res["run1"]["scan_ms_p50"] >= 0
    pools = {r["pool"] for r in rows if "pool" in r} | {r["mint"] for r in rows if "mint" in r}
    assert not any(p in out for p in pools)  # no pool or mint ever printed
    for line in out.splitlines():
        json.loads(line)


def _mk(tmp_path, name):
    base = tmp_path / "drill" / name
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    return base


def test_phantom_filter_flag_off_on_compare(tmp_path, _sandbox, capsys, monkeypatch):
    monkeypatch.setattr(H, "HV6_ROOT", str(tmp_path))
    write_layout(_sandbox, shifted_rows("2026-10-21"))
    t0 = RL.day_ms("2026-10-21") + 6 * HOUR
    win = (t0 + 9 * MIN + 2000, t0 + 70 * MIN)
    off = RL.run_real(["2026-10-21"], "toff", "a" * 40, base=_mk(tmp_path, "toff"), _window=win, phantom=False)
    on = RL.run_real(["2026-10-21"], "ton", "a" * 40, base=_mk(tmp_path, "ton"), _window=win, phantom=True, compare_off="toff")
    capsys.readouterr()
    assert off["phantom_filter"] == "off" and off["phantom_drops_by_kind"] is None and off["run1"]["phantom_drops"] is None
    assert on["phantom_filter"] == "on" and on["phantom_drops_by_kind"] == {"sig_ones": 0, "signer": 0, "repeat_slot": 0} and on["phantom_drops_total"] == 0
    c = on["compare_with_off"]
    assert c["log_md5_equal_to_off"] and c["cards_md5_equal_to_off"] and c["entries_equal_to_off"] and c["compare_ok"] and on["result"] == "PASS"
    assert c["cards_diff"] == 0 and c["closes_diff"] == 0 and "peak_rss_mb_diff" in c and off["run1_log_md5"] == on["run1_log_md5"]
    # md5 differs while nothing was dropped -> compare fails; with drops it is accepted
    f = tmp_path / "drill" / "toff" / "real-layout-result.json"
    f.chmod(0o644)
    d = json.loads(f.read_text())
    d["run1_log_md5"] = "0" * 32
    f.write_text(json.dumps(d))
    run1 = {"log_md5": on["run1_log_md5"], "cards_md5": on["run1_cards_md5"], "cards": on["run1"]["cards"], "closes": on["run1"]["closes"],
            "positions": on["run1"]["positions"], "entries": on["run1"]["entries"]}
    assert RL.compare_with_off("toff", run1, 1.0, 0)["compare_ok"] is False
    assert RL.compare_with_off("toff", run1, 1.0, 5)["compare_ok"] is True
    with pytest.raises(RL.Refused):
        RL.compare_with_off("../x", run1, 1.0, 0)


def test_phantom_filter_cli_flag_parses():
    import argparse  # the flag exists on the tool and defaults to on
    src = Path(RL.__file__).read_text()
    assert '"--phantom-filter", choices=("on", "off"), default="on"' in src and '"--compare-off"' in src
    sh = (Path(RL.__file__).resolve().parent.parent / "scripts/ops/hv6-real-layout-replay.sh").read_text()
    assert '--phantom-filter "$PF"' in sh and "--compare-off" in sh and argparse


# ------------------------------------------------------------------------------------------------------------------------ HV6 fixes A/B/C: drill-only switches
def test_legacy_constants_switch_restores_and_the_concurrent_path_reproduces_the_md5(tmp_path, _sandbox, capsys, monkeypatch):
    from tools import hv6_agents as A
    monkeypatch.setattr(H, "HV6_ROOT", str(tmp_path))
    write_layout(_sandbox, shifted_rows("2026-10-21"))
    t0 = RL.day_ms("2026-10-21") + 6 * HOUR
    win = (t0 + 9 * MIN + 2000, t0 + 70 * MIN)
    tokens, board = dict(A.MAX_TOKENS), H.BOARD_SIZE
    new = RL.run_real(["2026-10-21"], "tnew", "a" * 40, base=_mk(tmp_path, "tnew"), _window=win, phantom=False)
    leg = RL.run_real(["2026-10-21"], "tleg", "a" * 40, base=_mk(tmp_path, "tleg"), _window=win, phantom=False, legacy=True)
    assert dict(A.MAX_TOKENS) == tokens and H.BOARD_SIZE == board == 4 and A.MAX_TOKENS["entry"] == 1200    # the switch never leaks out of its run
    assert new["result"] == leg["result"] == "PASS" and leg["legacy_constants"] and leg["run1"]["legacy_constants"]
    assert new["run1_log_md5"] != leg["run1_log_md5"]               # max_tokens is in every llm_call row's request sha: the logs differ by design
    assert new["run1_cards_md5"] == leg["run1_cards_md5"]           # two pools: the board size cannot matter here, and max_tokens is not in a card
    legc = RL.run_real(["2026-10-21"], "tlegc", "a" * 40, base=_mk(tmp_path, "tlegc"), _window=win, phantom=False, legacy=True, concurrent=True,
                       expect_log_md5=leg["run1_log_md5"], expect_cards_md5=leg["run1_cards_md5"])
    assert legc["result"] == "PASS" and legc["expect_md5"]["ok"] and legc["concurrent_calls"] and legc["run1"]["counters"].get("concurrent_calls", 0) > 0
    bad = RL.run_real(["2026-10-21"], "tbad", "a" * 40, base=_mk(tmp_path, "tbad"), _window=win, phantom=False, expect_log_md5="0" * 32)
    capsys.readouterr()
    assert bad["result"] == "FAIL" and not bad["expect_md5"]["log_md5_equal"] and bad["md5_equal"]
    sh = (Path(RL.__file__).resolve().parent.parent / "scripts/ops/hv6-real-layout-replay.sh").read_text()
    assert '--legacy-constants "${HV6_LEGACY:-off}"' in sh and '--concurrent "${HV6_CONCURRENT:-off}"' in sh and "HV6_EXPECT_LOG_MD5" in sh
    assert RL.main(["--days", "2026-10-21", "--drill-id", "tx", "--sha", "a" * 40, "--expect-log-md5", "XYZ"]) == 2
