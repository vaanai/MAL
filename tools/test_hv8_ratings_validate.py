"""HV8 C3 tests: tools/hv8_ratings_validate.py on a synthetic dump manifest. No tape, no API. Nothing here says HV8 makes money."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import hv8_rate as H8
from tools import hv8_ratings_validate as V


def sha(i):
    return hashlib.sha256(f"c{i}".encode()).hexdigest()


def reply(f=1, d=2, t=3, why="f.vol_60m is high"):
    return json.dumps({"why": why, "flow": f, "dump_risk": d, "trend": t})


def make_dump(tmp, n=100):
    firsts = [sha(i) for i in range(n)]
    retest = H8.retest_select(firsts)
    plan = H8.split_batches(firsts, retest)
    d = tmp / "dump"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"batches": plan}))
    return d, plan, firsts, retest


def write_batches(tmp, plan, fn=lambda s, seq: reply(), skip=(), name="ratings"):
    r = tmp / name
    r.mkdir(exist_ok=True)
    for b in plan:
        seq = 0 if b["kind"] == "first" else 1
        lines = [json.dumps({"card_sha": s, "seq": seq, "reply": fn(s, seq)}) for s in b["shas"] if (s, seq) not in skip]
        (r / f"batch-{b['batch']:02d}.jsonl").write_text("\n".join(lines) + "\n")
    return r


def test_bar1_arithmetic():
    assert V.parse_bar1(95, 100) == {"valid_first": 95, "first_calls": 100, "need": 95, "pass": True}
    assert V.parse_bar1(94, 100)["pass"] is False
    assert V.parse_bar1(1269, 1336)["need"] == 1270 and V.parse_bar1(1269, 1336)["pass"] is False      # ceil(0.95 x 1336) = 1270


def test_clean_run_passes_bar1_and_reports_bars_2_and_3(tmp_path):
    d, plan, firsts, retest = make_dump(tmp_path)
    vary = lambda s, seq: reply(int(s[0], 16) % 4, int(s[1], 16) % 4, int(s[2], 16) % 4)  # noqa: E731
    res = V.validate(d, write_batches(tmp_path, plan, vary))
    assert res["bar1"]["pass"] and res["bar1"]["valid_first"] == 100 and res["replies"] == 110 and res["retest_pairs"] == 10
    assert res["bar2"]["kappa"]["flow"] == pytest.approx(1.0) and res["bar3"]["pass"]


def test_duplicate_card_sha_in_a_file_or_across_files_is_refused(tmp_path):
    d, plan, firsts, _ = make_dump(tmp_path)
    r = write_batches(tmp_path, plan)
    p = r / "batch-01.jsonl"
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines + [lines[0]]) + "\n")
    with pytest.raises(V.ValidationError, match="twice in one batch file"):
        V.validate(d, r)
    r2 = write_batches(tmp_path, plan, name="r2")
    (r2 / "batch-02.jsonl").write_text(lines[0] + "\n")     # a card of batch 1 in batch 2
    with pytest.raises(V.ValidationError):
        V.validate(d, r2)


def test_foreign_card_wrong_seq_unknown_batch_and_junk_are_refused(tmp_path):
    d, plan, firsts, retest = make_dump(tmp_path)
    r = write_batches(tmp_path, plan)
    (r / "batch-01.jsonl").write_text(json.dumps({"card_sha": sha(9999), "seq": 0, "reply": reply()}) + "\n")
    with pytest.raises(V.ValidationError, match="not in this batch"):
        V.validate(d, r)
    r = write_batches(tmp_path, plan, name="r2")
    s0 = plan[0]["shas"][0]
    (r / "batch-01.jsonl").write_text(json.dumps({"card_sha": s0, "seq": 1, "reply": reply()}) + "\n")
    with pytest.raises(V.ValidationError, match="seq does not match"):
        V.validate(d, r)
    r = write_batches(tmp_path, plan, name="r3")
    (r / "batch-77.jsonl").write_text("")
    with pytest.raises(V.ValidationError, match="unknown batch"):
        V.validate(d, r)
    (r / "batch-77.jsonl").unlink()
    (r / "notes.txt").write_text("x")
    with pytest.raises(V.ValidationError, match="unknown file"):
        V.validate(d, r)


def test_unrated_replies_missing_cards_and_reruns(tmp_path):
    d, plan, firsts, retest = make_dump(tmp_path)
    bad = set(firsts[:6])
    r = write_batches(tmp_path, plan, lambda s, seq: "not json" if (s in bad and seq == 0) else reply(), skip={(firsts[7], 0)})
    res = V.validate(d, r)
    assert res["unrated_reasons"] == {"not_json": 6} and res["missing_first"] == 1 and res["bar1"]["valid_first"] == 93 and not res["bar1"]["pass"]
    rb = [json.dumps({"card_sha": s, "seq": 2, "reply": reply()}) for s in firsts[:5]]       # 5% of 100 = 5 re-runs
    (r / f"batch-{plan[-1]['batch'] + 1:02d}.jsonl").write_text("\n".join(rb) + "\n")
    res = V.validate(d, r)
    assert res["reruns"] == 5 and res["bar1"]["valid_first"] == 98 and res["bar1"]["pass"]


def test_too_many_reruns_and_a_rerun_of_a_rated_card_are_refused(tmp_path):
    d, plan, firsts, _ = make_dump(tmp_path)
    r = write_batches(tmp_path, plan, lambda s, seq: "x" if seq == 0 else reply())
    no = plan[-1]["batch"] + 1
    (r / f"batch-{no:02d}.jsonl").write_text("\n".join(json.dumps({"card_sha": s, "seq": 2, "reply": reply()}) for s in firsts[:6]) + "\n")
    with pytest.raises(V.ValidationError, match="5% allowance"):
        V.validate(d, r)
    r = write_batches(tmp_path, plan, name="r2")
    (r / f"batch-{no:02d}.jsonl").write_text(json.dumps({"card_sha": firsts[0], "seq": 2, "reply": reply()}) + "\n")
    with pytest.raises(V.ValidationError, match="not an unrated"):
        V.validate(d, r)


def test_write_final_returns_the_manifest_sha_and_refuses_a_second_write(tmp_path):
    d, plan, firsts, _ = make_dump(tmp_path)
    r = write_batches(tmp_path, plan)
    res = V.validate(d, r)
    sha_ = V.write_final(d, r, res)
    assert hashlib.sha256((r / "manifest.json").read_bytes()).hexdigest() == sha_
    assert len((r / "first.jsonl").read_text().splitlines()) == 100
    with pytest.raises(V.ValidationError):
        V.write_final(d, r, res)
    with pytest.raises(V.ValidationError, match="final files"):
        V.validate(d, r)
