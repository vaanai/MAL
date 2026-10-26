"""Window end (entries halt, exits to completion), the START row with the RPC provider, the watch counters, and the regime-block producer job. Synthetic only."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import hv6_runner as R
from tools.test_hv4_runner import T0
from tools.test_hv6_regime_block import jl, rec
from tools.test_hv6_runner import _env, make, rows_of  # noqa: F401  (autouse: path allowlist for tmp dirs)

MIN = 60_000
REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ops" / "hv6-regime-block-job.sh"
START, END = T0 + 5 * MIN + 2000, T0 + 115 * MIN
HALT = T0 + 8 * MIN  # after the first entries (about T0 + 7 min), while their positions are open


def short_plan_script(req):
    """L and F buy the tagged cards with a SHORT plan (stop -7, target 12, 45 min) so their positions close inside the tape."""
    from tools.hv4_model import default_reply
    from tools.hv4_testkit import buy, reply, skip
    from tools.test_hv6_runner import tagged_ids

    if req.kind == "entry" and req.arm in ("L", "F"):
        return reply(*[buy(i, "CON") if i in tagged_ids(req) else skip(i) for i in req.ids])
    return default_reply(req)


def strip(rows):
    return [{k: v for k, v in r.items() if k not in ("seq", "prev", "sha")} for r in rows]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    from tools import hv6_cards as H

    mp = pytest.MonkeyPatch()
    base = str(tmp_path_factory.getbasetemp())
    mp.setattr(H, "READ_ROOTS", (base,))
    mp.setattr(H, "WRITE_ROOTS", (base,))
    mp.setenv("MAL_ARENA_TEST", "1")
    out = {}
    for name, halt in (("ref", None), ("halt", HALT), ("early", T0 + 5 * MIN + 30_000)):
        run = make(tmp_path_factory.mktemp("r6eg"), cfg_kw={"entries_halt_ms": halt}, model_script=short_plan_script)
        run.run(START, END)
        out[name] = run
    yield out
    mp.undo()


def test_no_entry_after_the_halt_and_exits_close_every_position(runs):
    ref, halt = runs["ref"], runs["halt"]
    ent_ref = [r["ms"] for a in R.ARM_ORDER for r in rows_of(ref, a, "entry")]
    ent = [r["ms"] for a in R.ARM_ORDER for r in rows_of(halt, a, "entry")]
    assert ent and max(ent) < HALT and sorted(ent) == sorted(e for e in ent_ref if e < HALT)
    assert len(rows_of(halt, "SCAN", "entries_halt")) == 1 and rows_of(halt, "SCAN", "entries_halt")[0]["open_positions"] > 0   # positions were open at the halt
    assert all(not b.open for b in halt.books.values())                                                                      # and code exits closed all of them
    assert sum(len(rows_of(halt, a, "sell")) for a in R.ARM_ORDER) >= len(ent) > 0
    after = lambda a, kinds: [r for r in halt.log.rows(a) if r["ms"] >= HALT and r["kind"] in kinds]  # noqa: E731
    for a in R.ARM_ORDER:
        assert not after(a, ("entry", "llm_call", "watch_add", "skip", "version"))                                            # no entry, no LLM call, no decision
    assert not [r for r in halt.log.rows("SCAN") if r["ms"] >= HALT and r["kind"] == "scan"]                                 # nothing but exits runs
    assert rows_of(halt, "SCAN", "run_complete") and halt._stop                                                              # it ends by itself once flat
    assert max(r["ms"] for a in R.ARM_ORDER for r in rows_of(halt, a, "sell")) > HALT


def test_behaviour_before_the_halt_is_byte_identical(runs):
    ref, halt = runs["ref"], runs["halt"]
    for name in (*R.ARM_ORDER, "SCAN"):
        a = strip(r for r in ref.log.rows(name) if r["ms"] < HALT)
        b = strip(r for r in halt.log.rows(name) if r["ms"] < HALT)
        assert a == b, name


def test_halt_before_any_entry_gives_none(runs):
    e = runs["early"]
    assert all(not rows_of(e, a, "entry") for a in R.ARM_ORDER) and rows_of(e, "SCAN", "run_complete") and rows_of(e, "SCAN", "entries_halt")[0]["open_positions"] == 0


def test_restart_after_the_halt_logs_nothing_twice_and_still_closes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r6egr")
    r1 = make(tmp, cfg_kw={"entries_halt_ms": HALT}, model_script=short_plan_script)
    r1.run(START, HALT + 2 * MIN)
    assert any(b.open for b in r1.books.values())
    del r1
    r2 = make(tmp, cfg_kw={"entries_halt_ms": HALT}, model_script=short_plan_script)
    r2.resume()
    r2.run(HALT + 2 * MIN, END)
    assert len(rows_of(r2, "SCAN", "entries_halt")) == 1 and len(rows_of(r2, "SCAN", "run_complete")) == 1 and all(not b.open for b in r2.books.values())
    for n in (*R.ARM_ORDER, "SCAN"):
        r2.log.verify(n)


def test_main_requires_a_pinned_halt_after_the_clock_and_logs_the_provider(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("r6egm")
    common = ["--root", str(base / "v6"), "--frozen-sha", "abc1234", "--clock-start-ms", "1000", "--feed-delay-slots", "2", "--stub"]
    assert R.main(common) == 2                                                         # no default for the window end
    assert R.main(common + ["--entries-halt-ms", "1000"]) == 2                         # not after the clock
    run = make(tmp_path_factory.mktemp("r6egs"))
    run.log_start("helius")
    row = rows_of(run, "SCAN", "start")[0]
    assert row["provider"] == "helius" and row["frozen_sha"] == "abcdef1234567" and "entries_halt_ms" in row


def test_watch_counters_added_expired_no_card_and_fired(tmp_path_factory):
    from tools.test_hv6_runner_qp918 import CARD, fake_runner, verdict

    r = fake_runner(tmp_path_factory.mktemp("r6egw"))
    w = {"terms": [["ret_15m", ">", 1.0]], "when": "ret_15m > 1", "ttl_min": 30}
    r._on_verdict("L", verdict("WATCH", watch=w, conviction=3), CARD, T0, T0)
    r._on_verdict("L", verdict("WATCH", watch=w, conviction=3), {**CARD, "id": "POOLY"}, T0 + 1, T0)
    assert r.counters["watch_added.L"] == 2
    r._fresh_card = lambda pool, T: None
    r.watches(T0 + MIN)
    assert r.counters["watch_no_card.L"] == 2 and len(r.books["L"].watches) == 2           # a watch with no card stays
    r.watches(T0 + 31 * MIN)
    assert r.counters["watch_expired.L"] == 2 and not r.books["L"].watches


# ------------------------------------------------------------------------------------------------------------------------ the regime-block producer job
def sha(p: Path) -> str:
    import hashlib

    return hashlib.sha256(p.read_bytes()).hexdigest()


def job_env(tmp, now=None, **over):
    out = tmp / "radar-out"
    out.mkdir(exist_ok=True)
    env = {**os.environ, "HV6_RADAR_CODE": str(REPO / "tools/change_radar.py"), "HV6_RADAR_CODE_SHA256": sha(REPO / "tools/change_radar.py"),
           "HV6_RADAR_SEALS": str(REPO / "tools/change_radar_seals.json"), "HV6_RADAR_WATCH": str(REPO / "tools/change_radar_watch.json"),
           "HV6_RADAR_WATCH_SHA256": sha(REPO / "tools/change_radar_watch.json"), "HV6_RADAR_OUT_DIR": str(out), "HV6_REGIME_DIR": str(tmp / "regime"),
           "HV6_BLOCK_TEST": "1", "HV6_BLOCK_ONCE": "1", "POLL_S": "1"}
    if now is not None:
        env["HV6_BLOCK_NOW"] = str(now)
    env.update(over)
    return env


def run_job(env):
    return subprocess.run(["sh", str(SCRIPT)], cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)


def test_job_writes_nightly_and_intraday_blocks_with_pins(tmp_path):
    day = datetime(2026, 10, 30, tzinfo=timezone.utc)
    now = int((day + timedelta(hours=7, minutes=15)).timestamp())
    env = job_env(tmp_path, now)
    (tmp_path / "radar-out" / "radar.jsonl").write_bytes(jl(rec(day + timedelta(hours=7, minutes=11), [])))
    r = run_job(env)
    assert r.returncode == 0, r.stdout + r.stderr
    nightly, intraday = int(day.timestamp()) * 1000 + 120_000, now * 1000
    reg = tmp_path / "regime"
    assert (reg / f"block-{nightly}.json").is_file() and (reg / f"block-{intraday}.json").is_file() and (reg / "pins.json").is_file()
    blk = json.loads((reg / f"block-{intraday}.json").read_text())
    assert blk["schema"] == "hv6_regime.v1" and blk["as_of_ms"] == intraday and blk["pin_mismatch"] == [] and blk["radar_status"] in ("ok", "partial", "stale")
    assert blk["pins"]["venue_tape"] == "" and blk["pins"]["seals_sha256"] == "5a2f8979c8493f7a3b200dd879f6f54c92a3bf383054af432c2fc35a8e10ca61"
    assert blk["input"]["n_records"] == 1 and (reg / f"radar-{intraday}.jsonl").is_file()
    assert (reg / f"block-{nightly}.json").read_text()  # the nightly block at 00:02Z is before the 00:05Z review
    pins = json.loads((reg / "pins.json").read_text())
    assert set(pins) == {"radar_code_path", "radar_code_sha256", "seals_path", "seals_sha256", "watch_path", "watch_sha256", "out_dir", "venue_tape"}
    again = run_job(env)                                                                    # idempotent: same as_of, same bytes
    assert again.returncode == 0, again.stdout + again.stderr


def test_job_loop_writes_an_intraday_block_when_the_radar_adds_a_record(tmp_path):
    out = tmp_path / "radar-out"
    out.mkdir()
    now = datetime.now(timezone.utc)
    (out / "radar.jsonl").write_bytes(jl(rec(now - timedelta(hours=2), [])))
    env = job_env(tmp_path, None, HV6_BLOCK_ONCE="0", POLL_S="1", STABLE_S="1")
    env.pop("HV6_BLOCK_NOW", None)
    p = subprocess.Popen(["sh", str(SCRIPT)], cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        reg = tmp_path / "regime"
        t_end = time.time() + 60
        while time.time() < t_end and len(list(reg.glob("block-*.json"))) < 2:  # the nightly block and the first intraday block
            time.sleep(0.3)
        n0 = len(list(reg.glob("block-*.json")))
        time.sleep(1.2)
        assert len(list(reg.glob("block-*.json"))) == n0                                    # no new radar record: no new block
        with open(out / "radar.jsonl", "ab") as f:
            f.write(jl(rec(datetime.now(timezone.utc) - timedelta(seconds=30), [])))
        t_end = time.time() + 30
        while time.time() < t_end and len(list(reg.glob("block-*.json"))) <= n0:
            time.sleep(0.3)
        assert len(list(reg.glob("block-*.json"))) > n0
    finally:
        p.kill()
        p.wait()
