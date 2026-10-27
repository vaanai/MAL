"""Tests for tools/hv7_rehearsal_validate.py: the QP-ruled bars, on made-up prompts and replies. No tape, no model, no outcome."""
from __future__ import annotations

import json
import math

import pytest

from tools import hv7_rehearsal_validate as V


def prompts(n_sets=72, n=8, sizes=None):
    out = {}
    for s in range(n_sets):
        k = sizes[s % len(sizes)] if sizes else n
        ids = [f"P{s}_{i}" for i in range(k)]
        rot = lambda xs, r: xs[r % len(xs):] + xs[:r % len(xs)]  # noqa: E731 - display orders: different rotations per set and shuffle (position-neutral)
        for tag, order in (("A", rot(ids, s)), ("B", rot(ids, s + 1))):
            out[f"batch-{s:02d}:{s}:{tag}"] = {"chunk_id": f"batch-{s:02d}", "set_id": s, "shuffle": tag, "card_ids": order}
    return out


def reply(ids):
    return json.dumps([{"id": i, "why": "f.vol_60m is highest"} for i in ids])


def replies(pr, pick):
    """pick(card_ids) -> the id placed first; the rest follow in display order."""
    out = {}
    for k, p in pr.items():
        first = pick(p["card_ids"])
        out[k] = reply([first] + [i for i in p["card_ids"] if i != first])
    return out


def leader(ids):
    return sorted(ids)[0]                        # content-driven and shuffle-independent: the same pool wins in A and B


def test_content_driven_ranker_passes_all_bars_and_is_content_driven():
    pr = prompts()
    r = V.validate(pr, replies(pr, leader))
    assert r["result"] == "PASS" and all(r["bars"].values()) and r["agreement"] == 1.0 and r["content_driven_report_only"]
    assert r["valid"] == 144 and r["valid_needed"] == 137 and r["slot1_bar"] == 0.25 and r["agreement_bar"] == pytest.approx(
        max(0.2, (72 / 8 + 2.1 * math.sqrt(72 * (1 / 8) * (7 / 8))) / 72))
    assert r["agreement_bar"] < 0.21 and r["n_histogram"] == {"8": 72}


def test_always_slot_one_fails_the_position_bar():
    pr = prompts()
    r = V.validate(pr, replies(pr, lambda ids: ids[0]))
    assert r["result"] == "FAIL" and r["bars"]["slot1"] is False and r["slot1_share"] == 1.0
    assert r["agreement"] == 0.0 and r["bars"]["consistency"] is False


def test_a_slot_other_than_one_above_twice_expected_fails_when_expected_is_at_least_9():
    pr = prompts()
    r = V.validate(pr, replies(pr, lambda ids: ids[2]))           # every pick at slot 3
    assert r["bars"]["other_slots"] is False and r["slots"]["3"]["bar_applies"] and r["slots"]["3"]["expected"] == pytest.approx(18.0)
    assert r["slots"]["3"]["observed"] == 144


def test_slots_with_expected_below_9_are_report_only():
    pr = prompts(n_sets=10, sizes=[3])                            # N = 3, 20 calls: E_2 = 20/3 < 9
    r = V.validate(pr, replies(pr, lambda ids: ids[1]))
    assert r["slots"]["2"]["bar_applies"] is False and r["bars"]["other_slots"] is True


def test_slot1_bar_uses_mean_of_inverse_n_not_inverse_mean_n():
    pr = prompts(sizes=[3, 8])
    mean_inv = (1 / 3 + 1 / 8) / 2
    r = V.validate(pr, replies(pr, leader))
    assert r["slot1_bar"] == pytest.approx(2 * mean_inv) and r["slot1_bar"] > 2 / 5.5          # Jensen: tighter if inverse-mean were used
    assert r["mean_inv_n"] == pytest.approx(mean_inv)


def test_parse_bar_is_ceil_95_percent_of_calls():
    pr = prompts()
    rep = replies(pr, leader)
    keys = sorted(rep)
    for k in keys[:7]:
        rep[k] = "not json"
    assert V.validate(pr, rep)["bars"]["parse"] is True               # 137 of 144 valid
    rep[keys[7]] = None                                               # a missing reply is a parse failure
    r = V.validate(pr, {k: v for k, v in rep.items() if v is not None})
    assert r["valid"] == 136 and r["bars"]["parse"] is False and r["result"] == "FAIL"


def test_consistency_bar_has_a_small_n_term_and_the_floor():
    pr = prompts(sizes=[3])                                          # chance agreement is 1/3: a random ranker must not pass
    keys = sorted(pr)
    rep = {}
    for k in keys:
        ids = pr[k]["card_ids"]
        pick = ids[0] if pr[k]["shuffle"] == "A" else ids[0]         # B is rotated by 1, so the slot-1 picks differ between A and B
        rep[k] = reply([pick] + [i for i in ids if i != pick])
    r = V.validate(pr, rep)
    assert r["agreement_bar"] > 1 / 3 and r["agreement"] == 0.0 and r["bars"]["consistency"] is False
    assert V.validate(prompts(), replies(prompts(), leader))["agreement_bar"] >= 0.2


def test_below_30_sets_is_not_decidable_and_asks_for_the_half_hour_rerun():
    pr = prompts(n_sets=29)
    r = V.validate(pr, replies(pr, leader))
    assert r["result"] == "NOT_DECIDABLE" and r["rerun_with_half_hour"] is True and r["sets"] == 29
    pr = prompts(n_sets=30)
    assert V.validate(pr, replies(pr, leader))["result"] in ("PASS", "FAIL")


def test_cli_without_a_manifest_is_refused(tmp_path):
    pr = prompts(n_sets=30)
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "batch-00.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pr.values()))
    rp = tmp_path / "replies.jsonl"
    rp.write_text("")
    assert V.main(["--prompts-dir", str(d), "--replies", str(rp)]) == 2


# ------------------------------------------------------------------------------------------------------------------------ input integrity
import hashlib  # noqa: E402


def write_run(tmp_path, pr, *, half=False, head="a" * 40, per_file=6):
    out = tmp_path / "run"
    (out / "prompts").mkdir(parents=True)
    items = list(pr.values())
    files = []
    for n, i in enumerate(range(0, len(items), per_file)):
        body = "".join(json.dumps(x, sort_keys=True) + "\n" for x in items[i:i + per_file]).encode()
        name = f"batch-{n:02d}.jsonl"
        (out / "prompts" / name).write_bytes(body)
        files.append({"file": f"prompts/{name}", "calls": len(items[i:i + per_file]), "sha256": hashlib.sha256(body).hexdigest()})
    man = {"calls": len(items), "sets": len({x["set_id"] for x in items}), "files": files, "half_hour": half}
    if head is not None:
        man["git_head"] = head
    (out / "manifest.json").write_text(json.dumps(man))
    return out


def write_replies(tmp_path, pr, extra=()):
    rep = replies(pr, leader)
    lines = [{"chunk_id": pr[k]["chunk_id"], "set_id": pr[k]["set_id"], "shuffle": pr[k]["shuffle"], "text": t} for k, t in rep.items()] + list(extra)
    rp = tmp_path / "replies.jsonl"
    rp.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return rp


def test_prompts_matching_the_manifest_load_and_print_head_and_half_hour(tmp_path):
    pr = prompts(n_sets=30)
    out = write_run(tmp_path, pr, half=False, head="c" * 40)
    p, r, m = V.load(out / "prompts", write_replies(tmp_path, pr))
    res = V.validate(p, r, manifest=m)
    assert res["git_head"] == "c" * 40 and res["half_hour"] is False and res["result"] == "PASS" and res["replies_matching_no_prompt"] == 0


@pytest.mark.parametrize("what", ["edit", "delete_file", "extra_file", "no_head", "short_head", "calls", "sets", "missing_manifest"])
def test_prompts_that_differ_from_the_manifest_are_refused(tmp_path, what):
    pr = prompts(n_sets=30)
    out = write_run(tmp_path, pr, head=None if what == "no_head" else ("abc" if what == "short_head" else "a" * 40))
    rp = write_replies(tmp_path, pr)
    files = sorted((out / "prompts").glob("batch-*.jsonl"))
    man = json.loads((out / "manifest.json").read_text())
    if what == "edit":
        files[0].write_bytes(files[0].read_bytes().replace(b"P0_0", b"P0_9"))
    elif what == "delete_file":
        files[-1].unlink()
    elif what == "extra_file":
        (out / "prompts" / "batch-99.jsonl").write_text("")
    elif what == "calls":
        man["calls"] += 1
    elif what == "sets":
        man["sets"] += 1
    elif what == "missing_manifest":
        (out / "manifest.json").unlink()
    if what in ("calls", "sets"):
        (out / "manifest.json").write_text(json.dumps(man))
    with pytest.raises(V.InputRefused):
        V.load(out / "prompts", rp)
    if what == "edit":                                                   # an edit that keeps the line count is still caught by the sha256
        assert V.main(["--prompts-dir", str(out / "prompts"), "--replies", str(rp)]) == 2


def test_duplicate_reply_keys_are_refused_and_strays_are_counted(tmp_path):
    pr = prompts(n_sets=30)
    out = write_run(tmp_path, pr)
    k = next(iter(pr.values()))
    dup = {"chunk_id": k["chunk_id"], "set_id": k["set_id"], "shuffle": k["shuffle"], "text": "[]"}
    with pytest.raises(V.InputRefused, match="duplicate_reply_key"):
        V.load(out / "prompts", write_replies(tmp_path, pr, extra=[dup]))
    assert V.main(["--prompts-dir", str(out / "prompts"), "--replies", str(tmp_path / "replies.jsonl")]) == 2
    stray = {"chunk_id": "batch-77", "set_id": 5, "shuffle": "A", "text": "x"}
    p, r, m = V.load(out / "prompts", write_replies(tmp_path, pr, extra=[stray]))
    res = V.validate(p, r, manifest=m)
    assert res["replies_matching_no_prompt"] == 1 and res["valid"] == 60 and res["result"] == "PASS"       # a stray is never used


def test_half_hour_manifest_under_30_sets_is_not_decidable_with_no_further_rerun(tmp_path):
    pr = prompts(n_sets=20)
    for half, want in ((False, True), (True, False)):
        sub = tmp_path / ("h" if half else "n")
        sub.mkdir()
        out = write_run(sub, pr, half=half)
        p, r, m = V.load(out / "prompts", write_replies(sub, pr))
        res = V.validate(p, r, manifest=m)
        assert res["result"] == "NOT_DECIDABLE" and res["rerun_with_half_hour"] is want and res["half_hour"] is half


def test_cli_prints_counts_only_and_exits_by_result(tmp_path, capsys):
    pr = prompts(n_sets=30)
    out = write_run(tmp_path, pr, head="d" * 40)
    rp = write_replies(tmp_path, pr)
    assert V.main(["--prompts-dir", str(out / "prompts"), "--replies", str(rp)]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["result"] == "PASS" and res["outcome_free"] is True and res["git_head"] == "d" * 40 and "P0_0" not in json.dumps(res)
