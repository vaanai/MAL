"""Tests for tools/haiku_v5_harness.py and tools/haiku_v5_export.py. Synthetic data only, stub model only: 0 API calls, no tape read."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import numpy as np
import pytest

from tools import haiku_v5_export as ex
from tools import haiku_v5_harness as h

DAYS = (["2026-08-%02d" % d for d in range(15, 28)] + ["2026-09-%02d" % d for d in range(4, 9)]
        + ["2026-09-%02d" % d for d in range(10, 15)] + ["2026-09-%02d" % d for d in (19, 20, 21)])  # 26 days inside allowed blocks


def blk_of(day: str) -> str:
    if day < "2026-08-28":
        return "explore-0814"
    if day < "2026-09-09":
        return "fresh-0903"
    if day < "2026-09-15":
        return "exp011-0909"
    return "fast-pool-0918"


def make_card(rng: random.Random, q: float) -> dict:
    card = {n: round(rng.uniform(0, 1), 4) for n in h.CARD_NAMES}
    card.update({"q_post_trig": q, "hour_utc": rng.randrange(24), "trig_seller_pool_bought": rng.random() < 0.5, "creator_pool_sold": None})
    return card


def synth(n_per_day: int = 45, days=DAYS, seed: int = 7):
    """Planted pattern: q_post_trig < 22 is a dead pool (flat -0.05 SOL); otherwise +0.004 + noise."""
    rng = random.Random(seed)
    trig, outc = [], {}
    for d in days:
        base = h.day_start_ms(d)
        for i in range(n_per_day):
            q = rng.uniform(18, 40)
            t = h.Trigger(f"t{d}{i:03d}", blk_of(d), base + 60_000 + i * 1_800_000 // max(1, n_per_day // 45) % (23 * 3_600_000),
                          0, make_card(rng, q))
            t = h.Trigger(t.tid, t.blk, t.decision_ms, t.decision_ms + h.OUTCOME_KNOWN_AFTER_MS, t.card)
            trig.append(t)
            flat = -0.05 if q < 22 else 0.004 + rng.gauss(0, 0.01)
            outc[t.tid] = {"flat_sol": flat, "press_sol": flat * 0.9}
    trig.sort(key=lambda t: (t.decision_ms, t.tid))
    return trig, outc


def write_inputs(tmp: Path, trig, outc, basis: str = "el2"):
    tp, op = tmp / "triggers.jsonl", tmp / "outcomes.jsonl"
    with open(tp, "w") as f:
        for t in trig:
            f.write(json.dumps({"type": "v5_trigger", "tid": t.tid, "blk": t.blk, "decision_ms": t.decision_ms,
                                "outcome_known_ms": t.outcome_known_ms, "card": dict(t.card)}) + "\n")
    with open(op, "w") as f:
        for tid, o in outc.items():
            f.write(json.dumps({"type": "v5_outcome", "tid": tid, "basis": basis, **o}) + "\n")
    return tp, op


class Recorder(h.StubModel):
    def __init__(self):
        super().__init__()
        self.decide_cards, self.feedbacks = [], []

    def decide(self, system, playbook, card_text):
        self.decide_cards.append((playbook, card_text))
        return super().decide(system, playbook, card_text)

    def reflect(self, system, playbook, feedback_text):
        self.feedbacks.append(feedback_text)
        return super().reflect(system, playbook, feedback_text)


# ------------------------------------------------------------------------------------------------------------------------ loading / seals
def test_load_triggers_and_refusals(tmp_path):
    trig, outc = synth(3, DAYS[:2])
    tp, op = write_inputs(tmp_path, trig, outc)
    got = h.load_triggers(tp)
    assert [t.tid for t in got] == [t.tid for t in trig]
    o, basis = h.load_outcomes(op, {t.tid for t in got})
    assert set(o) == {t.tid for t in trig} and basis == "el2"

    def bad(mut, msg):
        rows = [json.loads(x) for x in tp.read_text().splitlines()]
        mut(rows[0])
        p = tmp_path / "bad.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        with pytest.raises(h.Refused, match=msg):
            h.load_triggers(p)

    bad(lambda r: r.update(blk="fresh-0828"), "not in the allowed")
    bad(lambda r: r.update(decision_ms=h._ms(2026, 8, 28, 12), outcome_known_ms=h._ms(2026, 8, 28, 12) + h.OUTCOME_KNOWN_AFTER_MS),
        "outside its block window")
    bad(lambda r: r.update(flat_sol=0.1), "outcome field")
    bad(lambda r: r["card"].pop("q_post_trig"), "card fields")
    bad(lambda r: r.update(outcome_known_ms=r["decision_ms"]), "exactly decision_ms")
    bad(lambda r: r.update(outcome_known_ms=r["decision_ms"] + h.OUTCOME_KNOWN_AFTER_MS + 1), "exactly decision_ms")
    sealed = tmp_path / "x-shadow-y"
    sealed.mkdir()
    (sealed / "t.jsonl").write_text(tp.read_text())
    with pytest.raises(h.Refused, match="forbidden fragment"):
        h.load_triggers(sealed / "t.jsonl")


def test_october_and_all_blocks_end_before_october():
    assert all(hi <= h.NO_DATA_FROM_MS for _, hi in h.ALLOWED_BLOCKS.values())
    assert h.NO_DATA_FROM_MS == 1790812800000  # date -u -d @1790812800 = Thu Oct  1 00:00:00 UTC 2026
    assert h.ALLOWED_BLOCKS["explore-0814"] == (1786708800000, 1787918400000)  # 2026-08-14T12Z, 2026-08-28T12Z


def test_load_outcomes_refusals(tmp_path):
    trig, outc = synth(2, DAYS[:1])
    tp, op = write_inputs(tmp_path, trig, outc)
    tids = {t.tid for t in trig}
    with pytest.raises(h.Refused, match="cover"):
        h.load_outcomes(op, tids | {"extra"})
    p = tmp_path / "o2.jsonl"
    p.write_text(op.read_text().replace('"flat_sol": ', '"flat_sol": 1e9, "x": ', 1))
    with pytest.raises(h.Refused, match="finite SOL"):
        h.load_outcomes(p, tids)
    p.write_text(op.read_text().replace('"basis": "el2"', '"basis": "frozen"', 1))
    with pytest.raises(h.Refused, match="mix pricing bases"):
        h.load_outcomes(p, tids)
    p.write_text(op.read_text().replace('"basis": "el2", ', '', 1))
    with pytest.raises(h.Refused, match="without a basis"):
        h.load_outcomes(p, tids)


# ------------------------------------------------------------------------------------------------------------------------ parsing
def test_parse_decision_and_playbook():
    assert h.parse_decision('{"act": "SKIP", "size": 0.5, "why": "x"}') == {"act": False, "size": 0.5, "why": "x"}
    assert h.parse_decision('ok {"act": "BUY", "size": 2, "why": "y"} trailing')["act"] is True
    for bad in ('{"act": "HOLD", "size": 1}', '{"act": "BUY", "size": 3}', '{"act": "BUY", "size": true}', "nothing", None):
        assert h.parse_decision(bad) is None
    assert h.parse_playbook("a <playbook>R1</playbook> b <playbook>R2</playbook>") == "R2"
    assert h.parse_playbook("<playbook>" + "x" * (h.MAX_PLAYBOOK_CHARS + 1) + "</playbook>") is None
    assert h.parse_playbook("no tags") is None
    assert h.parse_playbook("<playbook>   </playbook>") is None


def test_derangement_no_fixed_point_and_deterministic():
    for n in range(2, 12):
        pi = h.derangement(n, "s")
        assert sorted(pi) == list(range(n)) and all(pi[i] != i for i in range(n))
        assert pi == h.derangement(n, "s")


def test_fmt_and_card_render():
    assert h.fmt(None) == "na" and h.fmt(float("nan")) == "na" and h.fmt(True) == "1" and h.fmt(3.0) == "3"
    assert h.fmt(0.123456) == "0.1235" and h.fmt("text") == "na"
    t = synth(1, DAYS[:1])[0][0]
    lines = h.render_card(t, "abc").splitlines()
    assert lines[0] == "CARD ref=abc" and [x.split(": ")[0] for x in lines[1:]] == list(h.CARD_NAMES)


# ------------------------------------------------------------------------------------------------------------------------ walk-forward
def test_walk_forward_no_leak_and_carry_over(tmp_path):
    trig, outc = synth(6, DAYS[:4])
    # one trigger 5 minutes before midnight: its outcome is known after the freeze, so it is fed one night later
    late = trig[5]
    d0 = late.day
    late2 = h.Trigger(late.tid, late.blk, h.day_start_ms(d0) + h.DAY_MS - 300_000, h.day_start_ms(d0) + h.DAY_MS - 300_000
                      + h.OUTCOME_KNOWN_AFTER_MS, late.card)
    trig = sorted([late2 if t.tid == late.tid else t for t in trig], key=lambda t: (t.decision_ms, t.tid))
    out = h.make_out_dir(tmp_path / "run")
    h.run_walk_forward(trig, outc, h.StubModel(), out, arms=("L", "P"), reps=1)
    logs = [json.loads(x) for x in (out / "feedback_log.jsonl").read_text().splitlines()]
    for g in logs:
        assert g["max_outcome_known_ms"] < g["freeze_ms"]
    first_L = [g for g in logs if g["arm"] == "L"][0]
    second_L = [g for g in logs if g["arm"] == "L"][1]
    assert late.tid not in first_L["tids"] and late.tid in second_L["tids"]
    # decisions of day D used the playbook that existed after the night before D, never a later one
    decs = [json.loads(x) for x in (out / "decisions.jsonl").read_text().splitlines()]
    by_day_pb = {}
    for g in logs:
        if g["arm"] == "L":
            by_day_pb[g["freeze_ms"]] = g["playbook_sha256"]
    for d in decs:
        if d["arm"] == "L" and h.day_start_ms(d["day"]) in by_day_pb:
            assert d["playbook_sha256"] == by_day_pb[h.day_start_ms(d["day"])]


def test_placebo_feedback_is_a_derangement_and_arms_see_same_cards(tmp_path):
    trig, outc = synth(8, DAYS[:2])
    rec = Recorder()
    h.run_walk_forward(trig, outc, rec, h.make_out_dir(tmp_path / "r"), arms=("L", "P"), reps=1)
    fl, fp = rec.feedbacks  # one night each, L first then P
    rl = [ln.rsplit(",", 1)[1] for ln in fl.splitlines()[2:-1]]
    rp = [ln.rsplit(",", 1)[1] for ln in fp.splitlines()[2:-1]]
    cards_l = [ln.rsplit(",", 1)[0] for ln in fl.splitlines()[2:-1]]
    cards_p = [ln.rsplit(",", 1)[0] for ln in fp.splitlines()[2:-1]]
    assert cards_l == cards_p and sorted(rl) == sorted(rp) and rl != rp
    # day-1 cards: identical text for L and P apart from the opaque nonce line, and the nonce carries no arm label
    n = len([t for t in trig if t.day == DAYS[0]])
    l_cards = [c for _, c in rec.decide_cards[:n]]
    p_cards = [c for _, c in rec.decide_cards[n:2 * n]]
    assert [c.split("\n", 1)[1] for c in l_cards] == [c.split("\n", 1)[1] for c in p_cards]
    assert all(c.splitlines()[0] != q.splitlines()[0] for c, q in zip(l_cards, p_cards))
    assert not any(("L1" in c.splitlines()[0] or "P1" in c.splitlines()[0]) for c in l_cards + p_cards)


def test_frozen_arm_never_reflects(tmp_path):
    trig, outc = synth(4, DAYS[:3])
    rec = Recorder()
    counts = h.run_walk_forward(trig, outc, rec, h.make_out_dir(tmp_path / "r"), arms=("F",), reps=2)
    assert rec.feedbacks == [] and all(c["reflections"] == 0 for c in counts.values())
    assert {pb for pb, _ in rec.decide_cards} == {h.PLAYBOOK_V0}


def test_parse_failures_fall_back_to_buy_and_are_counted(tmp_path):
    trig, outc = synth(10, DAYS[:1])
    out = h.make_out_dir(tmp_path / "r")
    counts = h.run_walk_forward(trig, outc, h.StubModel(broken_every=5), out, arms=("F",), reps=1)
    assert counts["F1"]["parse_fail"] == 2
    decs = [json.loads(x) for x in (out / "decisions.jsonl").read_text().splitlines()]
    assert sum(1 for d in decs if not d["parse_ok"]) == 2 and all(d["act"] for d in decs if not d["parse_ok"])


def test_feedback_sampling_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "MAX_FEEDBACK_ROWS", 5)
    trig, outc = synth(12, DAYS[:2])
    rec = Recorder()
    counts = h.run_walk_forward(trig, outc, rec, h.make_out_dir(tmp_path / "r"), arms=("L",), reps=1)
    assert len(rec.feedbacks[0].splitlines()) == 2 + 5 + 1 and counts["L1"]["feedback_sampled_out"] == 7


def test_write_once_dir(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(h.Refused, match="write-once"):
        h.make_out_dir(tmp_path / "x")


# ------------------------------------------------------------------------------------------------------------------------ scoring
def test_contrast_and_bootstrap_by_hand():
    a = {"d1": 3.0, "d2": 1.0, "d3": -1.0, "d4": 2.0}
    b = {k: 0.0 for k in a}
    c = h.contrast(a, b, ["d1", "d2", "d3", "d4"])
    assert c["total"] == 5.0 and c["mean_daily"] == 1.25 and c["ex_best_day"] == 2.0 and c["late_half"] == 1.0 and c["days_positive"] == 3
    assert -1.0 <= c["boot_lo5"] <= 1.25
    g = h.gate_stats_in_sample([0.1, -0.05, 0.2, 0.01, 0.02], ["a", "a", "b", "c", "c"])
    assert g["n"] == 5 and g["days"] == 3 and g["days_positive"] == 3 and abs(g["ex_top3_sol"] - (-0.04)) < 1e-12


def test_numeric_veto_uses_only_past_outcomes():
    trig, outc = synth(40, DAYS[:6])
    days = h.by_day(trig)
    k = {d: 5 for d, _ in days}
    v1 = h.numeric_veto(days, outc, k)
    target = days[4][0]
    outc2 = {tid: (dict(o, flat_sol=-o["flat_sol"] * 50) if next(t for t in trig if t.tid == tid).day >= target else o)
             for tid, o in outc.items()}
    v2 = h.numeric_veto(days, outc2, k)
    assert v1[target] == v2[target]  # flipping day D's (and later) outcomes cannot change D's veto
    assert v1[days[0][0]] == set() and all(len(v1[d]) == 5 for d, _ in days[3:])  # day 0: no history (< N_MIN_FIT)


def test_end_to_end_stub_run_and_score(tmp_path):
    trig, outc = synth(45)
    tp, op = write_inputs(tmp_path, trig, outc)
    assert h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--out-dir", str(tmp_path / "run"), "--model", "stub",
                   "--reps", "2"]) == 0
    assert h.main(["score", "--run-dir", str(tmp_path / "run"), "--triggers", str(tp), "--outcomes", str(op),
                   "--out", str(tmp_path / "score.json")]) == 0
    s = json.loads((tmp_path / "score.json").read_text())
    assert s["verdict"] in ("PASS_OFFLINE", "KILL_OFFLINE") and s["test_days"] == len(DAYS) - h.BURN_IN_DAYS
    lp = s["models"]["flat_sol"]["contrasts"]["L-P"]
    assert lp["total"] > 0  # the stub learns the planted dead-pool rule from true outcomes, not from deranged ones
    assert set(s["models"]) == {"flat_sol", "press_sol"} and "B1_parse_and_reflection" in s["bars"]
    assert s["models"]["flat_sol"]["L_rep1_kept_book_in_sample"]["label"] == "in-sample, report-only"
    with pytest.raises(FileExistsError):
        h.write_once_json(tmp_path / "score.json", {})


def test_score_not_decidable_when_small(tmp_path):
    trig, outc = synth(5, DAYS[:6])
    tp, op = write_inputs(tmp_path, trig, outc)
    assert h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--out-dir", str(tmp_path / "run"), "--model", "stub",
                   "--reps", "1"]) == 0
    assert h.main(["score", "--run-dir", str(tmp_path / "run"), "--triggers", str(tp), "--outcomes", str(op),
                   "--out", str(tmp_path / "s.json")]) == 0
    assert json.loads((tmp_path / "s.json").read_text())["verdict"] == "NOT_DECIDABLE"


def test_score_refuses_changed_inputs(tmp_path, capsys):
    trig, outc = synth(5, DAYS[:2])
    tp, op = write_inputs(tmp_path, trig, outc)
    assert h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--out-dir", str(tmp_path / "run"), "--model", "stub"]) == 0
    with open(op, "a") as f:  # any byte change: the sha256 no longer matches the run manifest
        f.write("\n")
    assert h.main(["score", "--run-dir", str(tmp_path / "run"), "--triggers", str(tp), "--outcomes", str(op),
                   "--out", str(tmp_path / "s.json")]) == 3


# ------------------------------------------------------------------------------------------------------------------------ CLI guards
def test_dry_mode_reads_no_outcome_and_bounds_cost(tmp_path, monkeypatch):
    trig, outc = synth(10, DAYS[:4])
    tp, _ = write_inputs(tmp_path, trig, outc)

    def boom(*a, **k):
        raise AssertionError("dry mode opened outcomes")

    monkeypatch.setattr(h, "load_outcomes", boom)
    assert h.main(["dry", "--triggers", str(tp), "--out-dir", str(tmp_path / "dry"), "--reps", "2"]) == 0
    d = json.loads((tmp_path / "dry" / "dry.json").read_text())
    c = d["cost"]
    assert c["calls"]["decide"]["n"] == 3 * 2 * len(trig) and c["calls"]["reflect"]["n"] == 2 * 2 * 3
    assert 0 < c["expected_usd"] < c["worst_case_usd"] and c["max_body_bytes"] < 100_000


def test_haiku_mode_refusals_never_construct_a_caller(tmp_path, monkeypatch):
    trig, outc = synth(3, DAYS[:1])
    tp, op = write_inputs(tmp_path, trig, outc)

    def boom(*a, **k):
        raise AssertionError("HaikuModel constructed")

    monkeypatch.setattr(h, "HaikuModel", boom)
    base = ["run", "--triggers", str(tp), "--outcomes", str(op), "--model", "haiku"]
    assert h.main(base + ["--out-dir", str(tmp_path / "a")]) == 3  # no --live / --approval
    full = base + ["--live", "--approval", "n_test", "--budget-usd", "1", "--ledger", str(tmp_path / "l.jsonl"), "--save-dir",
                   str(tmp_path / "s")]
    assert h.main(full + ["--out-dir", str(tmp_path / "b")]) == 3  # no --expect-sha256
    assert h.main(full + ["--out-dir", str(tmp_path / "c"), "--expect-sha256", f"triggers={h.sha256_file(tp)},outcomes=0"]) == 3
    assert h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--model", "stub", "--live", "--out-dir", str(tmp_path / "d")]) == 3
    pins = ["--expect-sha256", f"triggers={h.sha256_file(tp)},outcomes={h.sha256_file(op)}"]
    over = [x if x != "1" else "16.01" for x in full]
    assert h.main(over + pins + ["--out-dir", str(tmp_path / "e")]) == 3  # above the coded ledger ceiling
    pilot = [x if x != "1" else "1.01" for x in full]
    assert h.main(pilot + pins + ["--max-days", "1", "--out-dir", str(tmp_path / "f")]) == 3  # above the coded pilot ceiling
    tp2, op2 = write_inputs(tmp_path / "fz", trig, outc, basis="frozen") if (tmp_path / "fz").mkdir() is None else None
    full2 = [x if x not in (str(tp), str(op)) else (str(tp2) if x == str(tp) else str(op2)) for x in full]
    assert h.main(full2 + ["--expect-sha256", f"triggers={h.sha256_file(tp2)},outcomes={h.sha256_file(op2)}", "--out-dir",
                           str(tmp_path / "g")]) == 3  # a Haiku run on basis frozen
    assert not any((tmp_path / x).exists() for x in "abcdefg")
    assert h.LEDGER_BUDGET_MAX_USD == 16.0 and h.PILOT_BUDGET_MAX_USD == 1.0 and h.DECIDING_BASIS == "el2"


def test_haiku_model_request_shape():
    class FakeCaller:
        def __init__(self):
            self.calls = []

        def call(self, **kw):
            self.calls.append(kw)

            class R:
                text, cost_usd, request_sha256, replayed, usage = '{"act":"BUY","size":1,"why":"x"}', 0.0, "s", False, {}
            return R()

    m = h.HaikuModel.__new__(h.HaikuModel)
    m.caller = FakeCaller()
    m.decide("SYS", "PB", "CARD")
    m.reflect("SYS", "PB", "FB")
    d, r = m.caller.calls
    assert "thinking" not in d and d["max_tokens"] == h.DECIDE_MAX_TOKENS  # caller default: thinking disabled
    assert r["thinking"] is None and r["max_tokens"] == h.REFLECT_MAX_TOKENS  # omitted: adaptive
    assert d["system"][0]["cache_control"] == {"type": "ephemeral"} and d["system"][0]["text"].endswith("PLAYBOOK:\nPB")


def test_prompts_carry_no_outcome_statistics():
    for txt in (h.system_decide(), h.system_reflect(), h.PLAYBOOK_V0):
        assert "%" not in txt.replace("percent", "") and "11.5" not in txt and "30.7" not in txt
    assert "{" in h.system_decide() and "{glossary}" not in h.system_decide()


# ------------------------------------------------------------------------------------------------------------------------ export
def _export_rows():
    feats, trades = [], []
    for i, (day, blk, mbt_s, trunc) in enumerate([("2026-08-20", "explore-0814", 1787227200, False),
                                                   ("2026-08-20", "explore-0814", 1787227300, True),
                                                   ("2026-08-28", "explore-0814", 1787918251, False),  # 11:57:31Z: exit past block end
                                                   ("2026-09-10", "exp011-0909", 1789041600, False)]):
        mint = f"Mint{i}xyz"
        f = {"day": day, "mint": mint, "qa_exit_truncated": trunc, "f_s0_lag_slots": 2}
        f.update({"f_" + n: 1.5 for n in h.CARD_NAMES})
        f["f_t_trig_wall_s"] = 12.5  # also a card field; set after the fill
        f["f_creator_pool_sold"] = float("nan")
        feats.append(f)
        trades.append({"day": day, "mint": mint, "blk": blk, "mbt": mbt_s, "mbt_utc": mbt_s * 1000, "flat": 1e8, "press": 9e7})
    return feats, trades


def test_export_build_exclusions_and_opaque_tids():
    feats, trades = _export_rows()
    outc = ex.outcomes_from(trades, "frozen", None)
    with pytest.raises(h.Refused, match="need a basis"):
        ex.build(feats, trades, outc)
    res = ex.build(feats, trades, outc, "frozen")
    assert all(o["basis"] == "frozen" for o in res["outcomes"])
    assert res["excluded"] == {"qa_exit_truncated": 1, "outside_block_window": 1} and len(res["triggers"]) == 2
    t = res["triggers"][0]
    assert t["decision_ms"] == 1787227200 * 1000 + 2 * ex.SLOT_MS + 12_500 and t["outcome_known_ms"] - t["decision_ms"] == h.OUTCOME_KNOWN_AFTER_MS
    assert all("Mint" not in r["tid"] and "2026" not in r["tid"] and len(r["tid"]) == 16 for r in res["triggers"])
    assert t["card"]["creator_pool_sold"] is None and set(t["card"]) == set(h.CARD_NAMES)
    assert res["outcomes"][0]["flat_sol"] == 0.1 and "flat_sol" not in json.dumps(res["triggers"])


def test_export_refusals():
    feats, trades = _export_rows()
    with pytest.raises(h.Refused, match="disagree"):
        ex.build(feats, [dict(trades[0], mbt_utc=1)] + trades[1:])
    with pytest.raises(h.Refused, match="1:1"):
        ex.build(feats, trades + [dict(trades[0])])
    with pytest.raises(h.Refused, match="allowed"):
        ex.build(feats, [dict(trades[0], blk="fresh-0828")] + trades[1:])
    with pytest.raises(h.Refused, match="el2"):
        ex.outcomes_from(trades, "el2", None)


def test_export_with_outcomes_needs_a_basis(tmp_path):
    assert ex.main(["--out-dir", str(tmp_path / "e"), "--with-outcomes"]) == 3  # refused before pandas is imported
    assert not (tmp_path / "e").exists()


def test_export_parity(monkeypatch):
    feats, trades = _export_rows()
    outc = ex.outcomes_from(trades, "frozen", None)
    blk = {(r["day"], r["mint"]): r["blk"] for r in trades}
    monkeypatch.setitem(ex.PARITY, "frozen", {("disc", "flat_sol"): 0.3, ("conf", "flat_sol"): 0.1})
    assert ex.parity(outc, blk, "frozen")
    monkeypatch.setitem(ex.PARITY, "frozen", {("disc", "flat_sol"): 0.3002, ("conf", "flat_sol"): 0.1})
    assert not ex.parity(outc, blk, "frozen")
    el2 = [{"day": r["day"], "mint": r["mint"], "pnl_base_el2": 1e8, "p_fail": 0.2} for r in trades]
    o = ex.outcomes_from(trades, "el2", el2)[("2026-08-20", "Mint0xyz")]
    assert abs(o["flat_sol"] - (0.85e8 - 8250) / 1e9) < 1e-15 and abs(o["press_sol"] - (0.8e8 - 0.2 * 55000) / 1e9) < 1e-15


def test_caller_stop_exits_4_and_keeps_partial_output(tmp_path, monkeypatch):
    from tools.haiku_caller import BudgetExceeded

    trig, outc = synth(4, DAYS[:2])
    tp, op = write_inputs(tmp_path, trig, outc)

    class Stops(h.StubModel):
        def __init__(self, *a, **k):
            super().__init__()

        def decide(self, system, playbook, card_text):
            if self.calls >= 3:
                raise BudgetExceeded("over")
            return super().decide(system, playbook, card_text)

    monkeypatch.setattr(h, "HaikuModel", Stops)
    rc = h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--model", "haiku", "--live", "--approval", "n_test",
                 "--budget-usd", "1", "--ledger", str(tmp_path / "l.jsonl"), "--save-dir", str(tmp_path / "s"), "--out-dir", str(tmp_path / "r"),
                 "--expect-sha256", f"triggers={h.sha256_file(tp)},outcomes={h.sha256_file(op)}"])
    assert rc == 4 and len((tmp_path / "r" / "decisions.jsonl").read_text().splitlines()) == 3
    assert not (tmp_path / "r" / "manifest.json").exists()


# ------------------------------------------------------------------------------------------------------------------------ fixes for #1087
def test_qp1_crafted_outcome_known_row_is_refused(tmp_path):
    """QP's break: decision 23:59:30Z with outcome_known 1 ms later would have been fed that night. The loader must refuse it."""
    trig, outc = synth(3, DAYS[:2])
    tp, _ = write_inputs(tmp_path, trig, outc)
    rows = [json.loads(x) for x in tp.read_text().splitlines()]
    dec = h.day_start_ms(DAYS[0]) + h.DAY_MS - 30_000  # 23:59:30Z
    rows.append({"type": "v5_trigger", "tid": "tBAD", "blk": blk_of(DAYS[0]), "decision_ms": dec, "outcome_known_ms": dec + 1,
                 "card": dict(trig[0].card)})
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with pytest.raises(h.Refused, match="exactly decision_ms"):
        h.load_triggers(bad)
    rows[-1]["outcome_known_ms"] = dec + h.OUTCOME_KNOWN_AFTER_MS
    bad.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    got = {t.tid: t for t in h.load_triggers(bad)}
    assert got["tBAD"].outcome_known_ms == dec + h.OUTCOME_KNOWN_AFTER_MS > h.day_start_ms(DAYS[1])


def _stub_run(tmp_path, trig, outc, basis="el2", reps="2", name="run"):
    d = tmp_path / name
    d.mkdir()
    tp, op = write_inputs(d, trig, outc, basis=basis)
    assert h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--out-dir", str(d / "out"), "--model", "stub", "--reps", reps]) == 0
    return tp, op, d / "out"


def test_qp2_score_refuses_a_non_el2_basis(tmp_path):
    trig, outc = synth(45)
    tp, op, run = _stub_run(tmp_path, trig, outc, basis="frozen")
    assert json.loads((run / "manifest.json").read_text())["outcome_basis"] == "frozen"
    assert h.main(["score", "--run-dir", str(run), "--triggers", str(tp), "--outcomes", str(op), "--out", str(tmp_path / "s.json")]) == 3
    assert not (tmp_path / "s.json").exists()


def test_qp4_score_pins_harness_sha_and_records_the_run(tmp_path, monkeypatch, capsys):
    trig, outc = synth(45)
    tp, op, run = _stub_run(tmp_path, trig, outc)
    args = ["score", "--run-dir", str(run), "--triggers", str(tp), "--outcomes", str(op)]
    real = h.harness_sha256()
    monkeypatch.setattr(h, "harness_sha256", lambda: "0" * 64)
    assert h.main(args + ["--out", str(tmp_path / "s1.json")]) == 3 and not (tmp_path / "s1.json").exists()
    monkeypatch.setattr(h, "harness_sha256", lambda: real)
    assert h.main(args + ["--out", str(tmp_path / "s2.json")]) == 0
    s = json.loads((tmp_path / "s2.json").read_text())
    assert s["run"]["model"] == "stub" and s["run"]["reps"] == 2 and s["run"]["max_days"] == 0 and s["run"]["outcome_basis"] == "el2"
    assert s["run"]["harness_sha256_run"] == s["run"]["harness_sha256_score"] == real and s["o1_verdict_eligible"] is False
    assert "model stub; O1-eligible False" in capsys.readouterr().out
    assert h.o1_eligible({"model": "haiku", "reps": 3, "max_days": 0, "outcome_basis": "el2", "arms": ["L", "P", "F"]})
    assert not h.o1_eligible({"model": "haiku", "reps": 3, "max_days": 3, "outcome_basis": "el2", "arms": ["L", "P", "F"]})
    assert "skip_split_vs_A_report_only" in s["models"]["flat_sol"]


def test_score_refuses_missing_decisions(tmp_path):
    trig, outc = synth(45)
    tp, op, run = _stub_run(tmp_path, trig, outc)
    lines = (run / "decisions.jsonl").read_text().splitlines()
    (run / "decisions.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    assert h.main(["score", "--run-dir", str(run), "--triggers", str(tp), "--outcomes", str(op), "--out", str(tmp_path / "s.json")]) == 3


def test_qp5_capped_night_L_and_P_reflect_on_the_same_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "MAX_FEEDBACK_ROWS", 5)
    trig, outc = synth(20, DAYS[:3])
    out = h.make_out_dir(tmp_path / "r")
    h.run_walk_forward(trig, outc, h.StubModel(), out, arms=("L", "P"), reps=2)
    logs = [json.loads(x) for x in (out / "feedback_log.jsonl").read_text().splitlines()]
    by = {(g["arm"], g["rep"], g["after_day"]): g["tids"] for g in logs}
    for (arm, rep, day), tids in by.items():
        assert len(tids) == 5 and (arm != "L" or tids == by[("P", rep, day)])


def test_single_row_nights_counted(tmp_path):
    trig, outc = synth(1, DAYS[:3])
    counts = h.run_walk_forward(trig, outc, h.StubModel(), h.make_out_dir(tmp_path / "r"), arms=("P",), reps=1)
    assert counts["P1"]["single_row_nights"] == 2


def test_r2a_exact_freeze_boundary(tmp_path):
    """outcome_known == freeze waits a night; freeze - 1 is fed (a < turned into <= would fail this)."""
    trig, outc = synth(4, DAYS[:3])
    freeze = h.day_start_ms(DAYS[1])
    card = trig[0].card
    eq = h.Trigger("tEQ", blk_of(DAYS[0]), freeze - h.OUTCOME_KNOWN_AFTER_MS, freeze, card)
    m1 = h.Trigger("tM1", blk_of(DAYS[0]), freeze - h.OUTCOME_KNOWN_AFTER_MS - 1, freeze - 1, card)
    outc.update({"tEQ": {"flat_sol": 0.0, "press_sol": 0.0}, "tM1": {"flat_sol": 0.0, "press_sol": 0.0}})
    trig = sorted(trig + [eq, m1], key=lambda t: (t.decision_ms, t.tid))
    out = h.make_out_dir(tmp_path / "r")
    h.run_walk_forward(trig, outc, h.StubModel(), out, arms=("L",), reps=1)
    logs = [json.loads(x) for x in (out / "feedback_log.jsonl").read_text().splitlines()]
    assert logs[0]["freeze_ms"] == freeze and "tM1" in logs[0]["tids"] and "tEQ" not in logs[0]["tids"] and "tEQ" in logs[1]["tids"]


def test_r2b_numeric_fit_boundary_bites(tmp_path):
    """150 D-1 triggers whose outcomes land after D 00:00Z: flipping them cannot move D's veto. The same flip on rows known 1 ms before
    D 00:00Z does move it, so the test can fail."""
    rng = random.Random(3)
    d0, d1, d2 = DAYS[0], DAYS[1], DAYS[2]
    trig, outc = [], {}

    def add(tid, day, ms, known, q, flat):
        trig.append(h.Trigger(tid, blk_of(day), ms, known, make_card(rng, q)))
        outc[tid] = {"flat_sol": flat, "press_sol": flat}

    for i in range(120):
        q = rng.uniform(18, 40)
        ms = h.day_start_ms(d0) + 60_000 + i * 600_000
        add(f"a{i:03d}", d0, ms, ms + h.OUTCOME_KNOWN_AFTER_MS, q, -0.05 if q < 22 else 0.01)
    late = []
    for i in range(150):
        q = rng.uniform(18, 40)
        ms = h.day_start_ms(d2) - 899_000 + i * 5_000  # 23:45:01Z .. 23:57:26Z on d1
        late.append((f"b{i:03d}", ms, q))
    for i in range(40):
        q = rng.uniform(18, 40)
        ms = h.day_start_ms(d2) + 60_000 + i * 60_000
        add(f"c{i:03d}", d2, ms, ms + h.OUTCOME_KNOWN_AFTER_MS, q, 0.0)

    def run(known_after_midnight: bool, flipped: bool):
        t2, o2 = list(trig), dict(outc)
        for tid, ms, q in late:
            known = ms + h.OUTCOME_KNOWN_AFTER_MS if known_after_midnight else h.day_start_ms(d2) - 1
            t2.append(h.Trigger(tid, blk_of(d1), ms, known, make_card(random.Random(tid), q)))
            bad = q < 22
            flat = (-0.05 if bad else 0.01) if not flipped else (0.5 if bad else -0.05)
            o2[tid] = {"flat_sol": flat, "press_sol": flat}
        t2.sort(key=lambda t: (t.decision_ms, t.tid))
        return h.numeric_veto(h.by_day(t2), o2, {d0: 0, d1: 0, d2: 8})[d2]

    assert all(ms + h.OUTCOME_KNOWN_AFTER_MS > h.day_start_ms(d2) for _, ms, _ in late)
    assert run(True, False) == run(True, True)  # known after D 00:00Z: invisible to D's fit
    assert run(False, False) != run(False, True)  # known before: the flip moves D's veto


def test_r2c_feedback_guard_fails_closed():
    t = h.Trigger("tX", "explore-0814", h.day_start_ms(DAYS[1]) + 5, h.day_start_ms(DAYS[1]) - 1, {n: 0 for n in h.CARD_NAMES})
    st = h.ArmState("L", 1, pending=[(t, True)])
    with pytest.raises(h.Refused, match="not closed before the freeze"):
        h.feedback_rows(st, h.day_start_ms(DAYS[1]), {"tX": {"flat_sol": 0.0, "press_sol": 0.0}}, DAYS[0])


def test_r2c_playbook_guard_fails_closed(tmp_path, monkeypatch):
    trig, outc = synth(4, DAYS[:3])
    real = h.feedback_rows

    def leaky(st, freeze_ms, outcomes, day, placebo_text=None):
        rows, n, _ = real(st, freeze_ms, outcomes, day)
        return rows, n, freeze_ms  # pretend a row known AT the freeze was used
    monkeypatch.setattr(h, "feedback_rows", leaky)
    with pytest.raises(h.Refused, match="playbook built from an outcome known after the day start"):
        h.run_walk_forward(trig, outc, h.StubModel(), h.make_out_dir(tmp_path / "r"), arms=("L",), reps=1)


def test_r1_run_lock_refuses_a_second_process(tmp_path, monkeypatch):
    import fcntl

    trig, outc = synth(3, DAYS[:1])
    tp, op = write_inputs(tmp_path, trig, outc)
    ledger = tmp_path / "l.jsonl"

    def boom(*a, **k):
        raise AssertionError("HaikuModel constructed while the lock was held")

    monkeypatch.setattr(h, "HaikuModel", boom)
    fd = os.open(str(ledger) + h.RUN_LOCK_SUFFIX, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        rc = h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--model", "haiku", "--live", "--approval", "n_test",
                     "--budget-usd", "1", "--ledger", str(ledger), "--save-dir", str(tmp_path / "s"), "--out-dir", str(tmp_path / "r"),
                     "--expect-sha256", f"triggers={h.sha256_file(tp)},outcomes={h.sha256_file(op)}"])
    finally:
        os.close(fd)
    assert rc == 3 and not (tmp_path / "r").exists()
    with h.run_lock(str(ledger)):  # released: a later run gets it
        with pytest.raises(h.Refused, match="another v5 run"):
            with h.run_lock(str(ledger)):
                pass


def test_run_start_written_before_calls(tmp_path):
    trig, outc = synth(3, DAYS[:2])
    tp, op, run = _stub_run(tmp_path, trig, outc, reps="1")
    st = json.loads((run / "run_start.json").read_text())
    assert st["harness_sha256"] == h.harness_sha256() and st["prompt_sha256"] == h.PROMPT_SHAS and st["outcome_basis"] == "el2"
    assert st["args"]["model"] == "stub" and st["args"]["reps"] == 1


def test_check_path_follows_symlinks(tmp_path):
    sealed = tmp_path / "x-canary-y"
    sealed.mkdir()
    (sealed / "t.jsonl").write_text("{}\n")
    link = tmp_path / "innocent.jsonl"
    link.symlink_to(sealed / "t.jsonl")
    with pytest.raises(h.Refused, match="forbidden fragment"):
        h.check_path(link)


def test_pilot_structure_reads_logs_and_ledger_only(tmp_path):
    from tools.haiku_caller import ledger_summary

    run = tmp_path / "run"
    run.mkdir()
    (run / "decisions.jsonl").write_text("\n".join(json.dumps({"parse_ok": i != 0, "request_sha256": f"d{i}"}) for i in range(4)) + "\n")
    (run / "feedback_log.jsonl").write_text(json.dumps({"reflect_ok": True, "request_sha256": "r0"}) + "\n")
    store = tmp_path / "store"
    store.mkdir()
    (store / "d1.json").write_text(json.dumps({"request": {"x": "y" * 990}, "response": {}}))
    rows = []
    for i in range(4):  # decide: reserve 0.01 worst case, settle -0.009 -> 0.001 actual each
        rows.append({"kind": "reserve", "reservation_id": f"rd{i}", "request_sha256": f"d{i}", "cost_usd": 0.01})
        rows.append({"kind": "settle", "reservation_id": f"rd{i}", "request_sha256": f"d{i}", "http_status": 200, "input_tokens": 100,
                     "output_tokens": 50, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 0, "latency_ms": 900,
                     "stop_reason": "end_turn", "cost_usd": -0.009})
    rows.append({"kind": "reserve", "reservation_id": "rr0", "request_sha256": "r0", "cost_usd": 0.02})
    rows.append({"kind": "settle", "reservation_id": "rr0", "request_sha256": "r0", "http_status": 200, "input_tokens": 8000,
                 "output_tokens": 6000, "latency_ms": 50_000, "stop_reason": "max_tokens", "cost_usd": -0.015})
    rows.append({"kind": "reserve", "reservation_id": "rr1", "request_sha256": "r0", "cost_usd": 0.02})  # died mid-call: stays charged
    rows.append({"kind": "reserve", "reservation_id": "ro", "request_sha256": "zz", "cost_usd": 0.003})  # another run on the ledger
    rows.append({"kind": "settle", "reservation_id": "ro", "request_sha256": "zz", "http_status": 200, "cost_usd": -0.001})
    (tmp_path / "ledger.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    doc = h.pilot_structure(run, tmp_path / "ledger.jsonl", store)
    assert doc["decisions"] == 4 and doc["parse_ok_rate"] == 0.75 and doc["reflect_ok_rate"] == 1.0
    d, r, o = doc["by_kind"]["decide"], doc["by_kind"]["reflect"], doc["by_kind"]["other"]
    nbytes = len(json.dumps({"x": "y" * 990}, sort_keys=True, separators=(",", ":")).encode())
    assert d["n"] == 4 and d["cache_read_share"] == 0.75 and abs(d["input_tokens_per_byte"] - 400 / nbytes) < 1e-12
    assert abs(d["cost_usd"] - 0.004) < 1e-12 and abs(r["cost_usd"] - 0.025) < 1e-12 and abs(o["cost_usd"] - 0.002) < 1e-12
    assert d["attempts"] == 4 and r["attempts"] == 2 and r["latency_over_45s"] == 1 and r["max_tokens_stop_share"] == 1.0
    assert all(b["cost_usd"] >= 0 for b in doc["by_kind"].values())
    total = ledger_summary(tmp_path / "ledger.jsonl")["total_cost_usd"]
    assert abs(doc["total_cost_usd"] - total) < 1e-12 and doc["matches_ledger_summary"] is True
