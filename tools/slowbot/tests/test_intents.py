"""Live-intent gating: flag off leaves the paper runner's files byte-identical; flag on writes one row per H paper BUY. No network, no key."""
import json
import re

from tools.slowbot import intents as itn
from tools.slowbot.tests.test_runner import FakeCaller, mk, rows

SKIP = '{"action":"SKIP","conviction":2,"catalyst":"none","risk":"rug"}'


def run_dir(path, **kw):
    path.mkdir()
    r, fc = mk(path, price=True, **kw)
    r.run(duration=60)
    return r


def norm(raw):
    """The caller's wall-clock latency_ms is the only non-deterministic field in the paper files (tools/haiku_caller.py:854); mask it."""
    raw = re.sub(rb'"ts_utc": "2026-[^"]*"', b'"ts_utc": "WALL"', raw)  # decide.py stamps the real wall clock
    return re.sub(rb'"latency_ms": \d+', b'"latency_ms": 0', raw)


def files(path):
    return {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}


def test_flag_off_writes_no_intent_file_and_is_default(tmp_path):
    r = run_dir(tmp_path / "off", caller=FakeCaller())
    assert r.live_intents is False
    assert not (tmp_path / "off" / itn.FILENAME).exists()
    assert "live_intents_written" not in r.rs["counters"]


def test_flag_on_paper_files_byte_identical_except_counter(tmp_path):
    off = run_dir(tmp_path / "off", caller=FakeCaller())
    on = run_dir(tmp_path / "on", caller=FakeCaller(), live_intents=True)
    a, b = files(tmp_path / "off"), files(tmp_path / "on")
    assert set(b) - set(a) == {itn.FILENAME}
    for name in a:
        if name == "runner-state.json":
            continue
        assert norm(a[name]) == norm(b[name]), name
    so, sn = json.loads(a["runner-state.json"]), json.loads(b["runner-state.json"])
    sn["counters"].pop("live_intents_written")
    assert so == sn
    assert off.pm.state == on.pm.state


def test_flag_on_one_row_per_h_buy_with_paper_entry(tmp_path):
    r = run_dir(tmp_path / "on", caller=FakeCaller(), live_intents=True)
    got = itn.read_intents(tmp_path / "on" / itn.FILENAME)
    assert len(got) == 1 and r.rs["counters"]["live_intents_written"] == 1
    g = got[0]
    hpos = [x for x in rows(tmp_path / "on" / "positions.jsonl") if x["kind"] == "open" and x["arm"] == "H"][0]
    assert g["conviction"] == 4 and g["arm"] == "H" and g["intent_id"] == itn.intent_id(g["mint"], g["decision_ts"])
    assert g["paper_entry"]["in_lamports"] == hpos["cost_lamports"] and g["paper_entry"]["out_tokens"] == hpos["tokens"]
    assert g["pool"] and g["written_ts"] >= g["decision_ts"]


def test_h_skip_writes_no_intent(tmp_path):
    run_dir(tmp_path / "on", caller=FakeCaller(SKIP), live_intents=True)
    assert not (tmp_path / "on" / itn.FILENAME).exists()


def test_read_intents_skips_torn_and_foreign_rows(tmp_path):
    p = tmp_path / "i.jsonl"
    good = {"schema": itn.SCHEMA, "mint": "M"}
    p.write_text(json.dumps(good) + "\nnot json\n" + json.dumps({"schema": "x", "mint": "M"}) + '\n{"schema": "' + itn.SCHEMA + '", "mint"')
    assert itn.read_intents(p) == [good]
    assert itn.read_intents(tmp_path / "none") == []


def test_intent_carries_runner_sha_and_the_decision_rows_prompt_sha(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOT_RUNNER_SHA", "a" * 40)
    run_dir(tmp_path / "on", caller=FakeCaller(), live_intents=True)
    (g,) = itn.read_intents(tmp_path / "on" / itn.FILENAME)
    dec = [x for x in rows(tmp_path / "on" / "decisions.jsonl") if "prompt_sha256" in x][0]
    assert g["runner_sha"] == "a" * 40 and g["prompt_sha256"] == dec["prompt_sha256"] and len(g["prompt_sha256"]) == 64
