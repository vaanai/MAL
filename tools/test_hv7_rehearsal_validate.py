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


def test_cli_prints_counts_only_and_exits_by_result(tmp_path, capsys):
    pr = prompts(n_sets=30)
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "batch-00.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pr.values()))
    rp = tmp_path / "replies.jsonl"
    rep = replies(pr, leader)
    rp.write_text("".join(json.dumps({"chunk_id": pr[k]["chunk_id"], "set_id": pr[k]["set_id"], "shuffle": pr[k]["shuffle"], "text": t}) + "\n" for k, t in rep.items()))
    assert V.main(["--prompts-dir", str(d), "--replies", str(rp)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "PASS" and out["outcome_free"] is True and "P0_0" not in json.dumps(out)
