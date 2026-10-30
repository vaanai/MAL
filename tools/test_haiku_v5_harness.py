"""Tests for tools/haiku_v5_harness.py and tools/haiku_v5_export.py. Synthetic data only, stub model only: 0 API calls, no tape read."""

from __future__ import annotations

import json
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
