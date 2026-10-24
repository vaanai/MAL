"""Tests for tools/fasttrack_book.py. A throwaway git repo with an 'origin' stands in for vaanai/MAL; one optional test pins the example manifest on the real repo.
Nothing here registers a book, submits a job or opens /data."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools import fasttrack_book as FB

HERE = Path(__file__).resolve().parent.parent
EXAMPLE = HERE / "docs" / "research" / "p24-fast-track" / "example-floor.manifest.json"
NOW = FB.iso_to_s("2026-10-24T18:00:00Z")


def example() -> dict:
    return json.loads(EXAMPLE.read_text())


def failed(m: dict, now: int | None = NOW) -> set[str]:
    return {cid for cid, ok, _ in FB.lint(m, now) if not ok}


# ---------------------------------------------------------------- lint


def test_example_lints_clean():
    assert failed(example()) == set()


@pytest.mark.parametrize("mut,cid", [
    (lambda m: m.update(clock_iso="2026-10-26T01:00:00Z"), "M04_clock"),
    (lambda m: m.update(frozen_sha="5e12f8f5"), "M02_frozen_sha"),
    (lambda m: m.update(horizon_days=14), "M06_job_limit"),
    (lambda m: m.update(slip_days=[0, 1, 2]), "M05_slips"),
    (lambda m: m.update(start_margin_s=0), "M07_start_lag"),
    (lambda m: m["caps"].update(feed_gap_pct=8), "M14_caps"),
    (lambda m: m["cost"].update(fail_models=["flat15"]), "M15_fail_models"),
    (lambda m: m["cost"].update(deciding_leg_s=1.3), "M16_legs"),
    (lambda m: m["cost"].update(pumpswap_virtual_reserve=False), "M17_virtual_reserve"),
    (lambda m: m["cost"].update(reserve_convention="post-trade"), "M17_virtual_reserve"),
    (lambda m: m["paths"].update(smoke_root=m["paths"]["root"]), "M12_smoke_isolated"),
    (lambda m: m["paths"].update(smoke_root="/data/mal/arena"), "M12_smoke_isolated"),
    (lambda m: m["paths"].update(root="/data/mal/hunt-1008/fwd-v1/x"), "M11_paths_forbidden"),
    (lambda m: m["paths"].update(root="/data/mal/arena/probe-x"), "M11_paths_forbidden"),
    (lambda m: m["paths"].update(tape="data/tape"), "M10_paths_abs"),
    (lambda m: m["thresholds"]["max_gap_s"].update(label="load"), "M13_thresholds"),
    (lambda m: m.update(host="mal-fast-0"), "M19_job_memory"),
    (lambda m: m["smoke"].update(hours=1), "M20_smoke"),
    (lambda m: m.update(tests=[]), "M21_tests"),
    (lambda m: m["premortem"].pop("PM05_day1_dry_score"), "M23_premortem"),
    (lambda m: m["premortem"].update(PM09_capacity="FILL later"), "M24_placeholders"),
    (lambda m: m.update(preconditions=["p0"]), "M25_preconditions"),
    (lambda m: m.update(tool_refs={}), "M26_tool_refs"),
    (lambda m: m.update(bot_id="floor-probe-1"), "M03_bot_id"),
])
def test_each_check_fails_on_its_defect(mut, cid):
    m = example()
    mut(m)
    assert cid in failed(m)


def test_deadline_passed():
    assert "M09_deadline_open" in failed(example(), FB.iso_to_s("2026-10-26T23:50:00Z"))
    assert "M09_deadline_open" not in failed(example(), FB.iso_to_s("2026-10-26T23:44:59Z"))


def test_llm_book_needs_real_model_smoke_and_pm13():
    m = example()
    m["llm"] = {"model_ids": ["claude-haiku-5-5"], "sampling": {"thinking": "off"}, "max_parse_fail_pct": 5, "usd_per_day_cap": 4.7}
    assert "M23_premortem" in failed(m)  # PM13 now required
    m["premortem"]["PM13_llm"] = "real-model smoke 2 h, parse <= 5%, $4.70/day cap x 12 d = $56.40 under the $200 cap"
    assert failed(m) == set()
    m["llm"]["stub_smoke"] = True
    assert "M22_llm" in failed(m)


def test_missing_fields_stop_early():
    assert failed({"schema": FB.SCHEMA}) == {"M00_fields"}


# ---------------------------------------------------------------- schedule arithmetic (FLOOR defect: "CLOCK - 24 h 15 min" OK'd twice)


def test_schedule_is_computed_not_typed():
    s = FB.schedule(example())
    c0, c1 = s["clocks"]
    assert c0["clock_iso"] == "2026-10-26T00:00:00Z" and c1["clock_iso"] == "2026-10-27T00:00:00Z"
    assert c0["register_deadline_iso"] == "2026-10-25T23:45:00Z"
    assert c0["run_start_iso"] == "2026-10-25T23:35:00Z"
    assert c0["end_h_iso"] == "2026-11-07T00:00:00Z" and c0["end_book_iso"] == "2026-11-08T01:00:00Z"
    assert c0["run_limit_s"] == (FB.iso_to_s("2026-11-08T01:00:00Z") - FB.iso_to_s("2026-10-25T23:35:00Z")) + 3600
    assert c0["latest_start_s"] == FB.iso_to_s("2026-10-25T23:45:00Z")


# ---------------------------------------------------------------- sh lint


def test_lint_sh_catches_bashisms_not_embedded_python():
    assert FB.lint_sh("a | b; echo ${PIPESTATUS[0]}") == ["PIPESTATUS"]
    assert "[[" in FB.lint_sh("if [[ -e x ]]; then :; fi")
    assert "pipefail" in FB.lint_sh("set -o pipefail")
    assert "array" in FB.lint_sh("X=(a b)")
    assert FB.lint_sh("# [[ in a comment\n[ -e x ] && echo ok") == []
    assert FB.lint_sh("python - <<'PYEOF'\nx = [[1]]\nPYEOF\necho ok") == []


# ---------------------------------------------------------------- throwaway repo with an origin


def _git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()


FILES = {
    "tools/arena_rules.py": "TICK_MS = 1_000\nSTATUS_EVERY_TICKS = 600\n",
    "tools/arena_rules_v2.py": 'RULE_MODULES_V2 = {"l1-floor-v2": "tools.arena_rule_l1"}\n',
    "tools/arena_stack.py": "SEND_FEE_DECIDING = 505_000\nRENT_DECIDING = 0\n",
    "tools/arena_feed.py": "x = 1\n", "tools/arena_paper.py": "x = 2\n", "tools/arena_score.py": "x = 3\n",
    "tools/arena_rule_l1.py": "x = 4\n", "tools/arena_pool_table.py": "x = 5\n",
    "tools/test_arena_rule_l1.py": "def test_x():\n    pass\n", "tools/test_arena_rules.py": "def test_y():\n    pass\n",
    "scripts/research/arena-rules-v2-job.sh": "#!/bin/sh\necho host\n",
}
TOOLS = {
    "tools/arena_hour_health.py": 'FEED_KEYS = ("feed_max_nonnull_gap_s", "feed_writing_paused", "feed_pauses_delta", "feed_rows_dropped_delta")\n'
                                  'THRESHOLD_KEYS = ("max_gap_s", "max_data_loss_count", "min_samples_per_hour", "max_sample_gap_s", "max_input_age_s") + FEED_KEYS\n',
    "tools/floor_p0_count.py": "x = 6\n",
}


@pytest.fixture()
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    r = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    for p, t in FILES.items():
        (r / p).parent.mkdir(parents=True, exist_ok=True)
        (r / p).write_text(t)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "book")
    frozen = _git(r, "rev-parse", "HEAD")
    for p, t in TOOLS.items():
        (r / p).write_text(t)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "tools")
    tool = _git(r, "rev-parse", "HEAD")
    _git(r, "remote", "add", "origin", str(origin))
    _git(r, "push", "-q", "origin", "main")
    _git(r, "fetch", "-q", "origin")
    m = example()
    m["frozen_sha"] = frozen
    m["tool_refs"] = {p: tool for p in TOOLS}
    m["pack_dir"] = str(tmp_path / "pack")
    return r, m, frozen, tool


def test_pin_reads_code_at_frozen_sha(repo):
    r, m, frozen, tool = repo
    p = FB.pin(m, str(r))
    assert p["on_origin_main"] is True
    assert p["cadence"]["status_lines_per_hour"] == 6  # FLOOR smoke said ">= 10 per hour"
    assert p["pins"]["tools/arena_feed.py"] == hashlib.sha256(b"x = 1\n").hexdigest()
    assert p["tool_pins"]["tools/arena_hour_health.py"]["commit"] == tool
    assert {c["name"]: c["value"] for c in p["constants"]} == {"SEND_FEE_DECIDING": 505000, "RENT_DECIDING": 0}


def test_pin_refusals(repo):
    r, m, frozen, tool = repo
    m2 = copy.deepcopy(m); m2["cost"]["send_lamports"] = 160_000  # DEC text vs code (FLOOR Am.9 item 9)
    with pytest.raises(FB.Refused, match="SEND_FEE_DECIDING"):
        FB.pin(m2, str(r))
    m3 = copy.deepcopy(m); m3["rule_names"] = ["l9-nope"]
    with pytest.raises(FB.Refused, match="not in tools/arena_rules_v2.py"):
        FB.pin(m3, str(r))
    m4 = copy.deepcopy(m); m4["thresholds"]["max_load"] = {"value": 1, "label": "decision-input"}
    with pytest.raises(FB.Refused, match="unknown to"):
        FB.pin(m4, str(r))
    m5 = copy.deepcopy(m); m5["tests"] = ["tools/test_missing.py"]  # an empty or wrong glob (FLOOR L3)
    with pytest.raises(FB.Refused, match="not in frozen_sha"):
        FB.pin(m5, str(r))
    m6 = copy.deepcopy(m); m6["tool_refs"] = {}  # hour-health is not in the frozen sha
    with pytest.raises(FB.Refused):
        FB.pin(m6, str(r))
    # a local-only commit is not on origin
    (r / "tools/arena_feed.py").write_text("x = 9\n")
    _git(r, "commit", "-qam", "local")
    m7 = copy.deepcopy(m); m7["frozen_sha"] = _git(r, "rev-parse", "HEAD")
    with pytest.raises(FB.Refused, match="no origin branch"):
        FB.pin(m7, str(r))


def test_render_verify_and_stale_pin(repo, tmp_path):
    r, m, frozen, tool = repo
    out = Path(m["pack_dir"])
    files = FB.render(m, FB.pin(m, str(r)), out)
    assert {"register.sh", "run.sh", "smoke.sh", "smoke-check.sh", "jobs.json", "REGISTRATION.md", "PINS.sha256", "SHA256SUMS", "thresholds.json"} <= set(files)
    for n in files:
        if n.endswith(".sh"):
            assert FB.lint_sh(files[n]) == [], n
            subprocess.run(["sh", "-n", str(out / n)], check=True)
    assert "FILL" not in files["REGISTRATION.md"] and "TBD" not in files["REGISTRATION.md"]
    assert "status lines >= 9 over 2 h (the host emits 6 per hour" in files["blocking-list.txt"]
    assert "2026-10-25T23:45:00Z" in files["REGISTRATION.md"]
    for x in m["preconditions"]:
        assert f"PRECONDITION-{x}.ok" in files["register.sh"]
    jobs = json.loads(files["jobs.json"])["jobs"]
    assert all(j["git_ref"] == frozen for j in jobs)
    run = [j for j in jobs if j["name"].endswith(" run")][0]
    assert run["resumable"] and run["time_limit_min"] * 60 <= FB.PLATFORM_MAX_LIMIT_S + 60
    assert hashlib.sha256(files["thresholds.json"].encode()).hexdigest() in files["REGISTRATION.md"]
    # the pinned thresholds are byte-identical to FLOOR's L7 file (same key order free, sorted canonical form)
    assert json.loads(files["thresholds.json"]) == m["thresholds"]
    # verify passes, then catches a tampered pack file
    assert all(ok for _, ok, _ in FB.verify(out, str(r)))
    (out / "run.sh").write_text(files["run.sh"] + "# edited\n")
    assert not all(ok for _, ok, _ in FB.verify(out, str(r)))
    # a pack is written once
    with pytest.raises(FB.Refused, match="not empty"):
        FB.render(m, FB.pin(m, str(r)), out)
    # PINS.sha256 catches a stale pinned file in the checkout (what every generated script runs first)
    _git(r, "checkout", "-q", frozen)
    assert subprocess.run(["sha256sum", "-c", "--quiet", str(out / "PINS.sha256")], cwd=r).returncode == 0
    (r / "tools/arena_paper.py").write_text("x = 22\n")
    assert subprocess.run(["sha256sum", "-c", "--quiet", str(out / "PINS.sha256")], cwd=r, capture_output=True).returncode != 0


def test_generated_register_refuses_without_preconditions(repo):
    r, m, frozen, tool = repo
    m["host"] = "mal-research-0"
    out = Path(m["pack_dir"])
    FB.render(m, FB.pin(m, str(r)), out)
    _git(r, "checkout", "-q", frozen)
    host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    if host != "mal-research-0":
        pytest.skip("host guard runs first off mal-research-0")
    res = subprocess.run(["sh", str(out / "register.sh")], cwd=r, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "CLOCK_ISO": "2026-10-26T00:00:00Z"})
    assert res.returncode != 0 and "REFUSED" in res.stdout


def test_cli_lint_and_render(repo, capsys):
    r, m, frozen, tool = repo
    mp = Path(m["pack_dir"]).parent / "m.json"
    mp.write_text(json.dumps(m))
    assert FB.main(["lint", str(mp), "--now-iso", "2026-10-24T18:00:00Z"]) == 0
    assert FB.main(["render", str(mp), "--repo", str(r), "--out", m["pack_dir"], "--now-iso", "2026-10-24T18:00:00Z"]) == 0
    assert FB.main(["verify", m["pack_dir"], "--repo", str(r)]) == 0
    bad = dict(m, clock_iso="2026-10-26T01:00:00Z")
    mp.write_text(json.dumps(bad))
    assert FB.main(["lint", str(mp)]) == 1


def test_example_pins_on_the_real_repo():
    """FLOOR's hand pins, reproduced by the generator: the hour-health tool sha256 227f92be (L7-pin-floor.sh) and 6 status lines per hour."""
    try:
        _git(HERE, "cat-file", "-e", "5e12f8f597571d6d5191a7ba38125c67ad723b7d^{commit}")
        _git(HERE, "cat-file", "-e", "0a5aef86880370218fb288a3df40cb72a6084b20^{commit}")
    except subprocess.CalledProcessError:
        pytest.skip("FLOOR commits not in this clone")
    p = FB.pin(example(), str(HERE))
    assert p["tool_pins"]["tools/arena_hour_health.py"]["sha256"] == "227f92be466fec0bd6c6502bcece6b2e7755d0a675a30d08a2dd79d6353903b5"
    assert p["cadence"]["status_lines_per_hour"] == 6
