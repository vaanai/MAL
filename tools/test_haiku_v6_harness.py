"""Tests for tools/haiku_v6_harness.py. Synthetic data only, stub model only: 0 API calls, no tape read, no outcome file of the lab."""

from __future__ import annotations

import inspect
import json
import os
import random
import re
from pathlib import Path

import numpy as np
import pytest

from tools import haiku_v6_harness as h

DAYS = (["2026-08-%02d" % d for d in range(15, 28)] + ["2026-09-%02d" % d for d in range(4, 9)]
        + ["2026-09-%02d" % d for d in range(10, 15)] + ["2026-09-%02d" % d for d in (19, 20, 21)])  # 26 days inside allowed blocks
SHIFT_DAY = DAYS[5]  # the planted tail field changes here: a frozen night-1 playbook goes stale, a learner re-learns
TAIL = 0.1  # flat_sol at or above this is a planted tail winner


def blk_of(day: str) -> str:
    if day < "2026-08-28":
        return "explore-0814"
    if day < "2026-09-09":
        return "fresh-0903"
    if day < "2026-09-15":
        return "exp011-0909"
    return "fast-pool-0918"


def make_card(rng: random.Random) -> dict:
    card = {n: round(rng.uniform(0, 1), 4) for n in h.CARD_NAMES}
    card.update({"q_post_trig": round(rng.uniform(18, 40), 3), "hour_utc": rng.randrange(24), "trig_seller_pool_bought": rng.random() < 0.5,
                 "creator_pool_sold": None})
    return card


def synth(n_per_day: int = 45, days=DAYS, seed: int = 7):
    """Planted right tail: a trigger whose tail field exceeds 0.8 pays +0.25 SOL (flat), everything else -0.01, plus noise. The tail field is
    top3_seller_share before SHIFT_DAY and trig_sell_frac_Qpre from SHIFT_DAY on. press = 0.9 x flat."""
    rng = random.Random(seed)
    trig, outc = [], {}
    for d in days:
        base = h.day_start_ms(d)
        for i in range(n_per_day):
            card = make_card(rng)
            ms = base + 60_000 + i * (22 * 3_600_000 // max(1, n_per_day))
            t = h.Trigger(f"t{d}{i:03d}", blk_of(d), ms, ms + h.OUTCOME_KNOWN_AFTER_MS, card)
            trig.append(t)
            field = "top3_seller_share" if d < SHIFT_DAY else "trig_sell_frac_Qpre"
            flat = (0.25 if card[field] > 0.8 else -0.01) + rng.gauss(0, 0.01)
            outc[t.tid] = {"flat_sol": flat, "press_sol": flat * 0.9}
    trig.sort(key=lambda t: (t.decision_ms, t.tid))
    return trig, outc


def write_inputs(tmp: Path, trig, outc, basis: str = "el2"):
    tmp.mkdir(parents=True, exist_ok=True)
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
    """The stub, recording every request it is asked to send: (kind, system, playbook, user)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls_log = []

    def decide(self, system, playbook, card_text):
        self.calls_log.append(("decide", system, playbook, card_text))
        return super().decide(system, playbook, card_text)

    def reflect(self, system, playbook, feedback_text):
        self.calls_log.append(("reflect", system, playbook, feedback_text))
        return super().reflect(system, playbook, feedback_text)


def stub_run(tmp_path, trig, outc, reps="2", name="run", basis="el2"):
    d = tmp_path / name
    tp, op = write_inputs(d, trig, outc, basis=basis)
    assert h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--out-dir", str(d / "out"), "--model", "stub", "--reps", reps]) == 0
    return tp, op, d / "out"


def write_decisions(run_dir: Path, trig, policy, reps=(1, 2, 3), parse_ok=lambda arm, rep, t: True):
    """Hand-made decisions.jsonl: policy(arm, rep, trigger) -> boost?"""
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "decisions.jsonl", "w") as f:
        for arm in h.LLM_ARMS:
            for rep in reps:
                for t in trig:
                    f.write(json.dumps({"arm": arm, "rep": rep, "day": t.day, "tid": t.tid, "boost": bool(policy(arm, rep, t)),
                                        "parse_ok": bool(parse_ok(arm, rep, t))}) + "\n")


GOOD_COUNTS = {f"L{r}": {"reflections": 25, "reflect_fail": 0} for r in (1, 2, 3)}


# ------------------------------------------------------------------------------------------------------------------------ parsing and the cap
def test_parse_call_boost_or_normal_only():
    assert h.parse_call('{"call": "boost", "why": "x"}') == {"boost": True, "why": "x"}
    assert h.parse_call('ok {"call": " Normal ", "why": "y"} tail') == {"boost": False, "why": "y"}
    assert h.parse_call('{"call": "BOOST"}')["boost"] is True
    # there is no way to say skip: every other word, and the v5 BUY/SKIP format, is a parse failure
    for bad in ('{"call": "skip"}', '{"act": "SKIP", "size": 1}', '{"act": "BUY", "size": 2}', '{"call": "double"}', '{"call": true}',
                '{"call": null}', "boost", "nothing", None, 7):
        assert h.parse_call(bad) is None


def test_cap_playbook_boundaries():
    cap = h.MAX_PLAYBOOK_CHARS
    assert cap == 2000
    ok = "x" * cap
    assert h.cap_playbook(ok) == (ok, False)
    assert h.cap_playbook("  " + ok + "\n") == (ok, False)  # surrounding whitespace is not counted
    cut, tr = h.cap_playbook("y" * (cap + 1))
    assert cut == "y" * cap and tr is True  # no newline: hard cut at the cap
    lines = ["BOOST if top3_seller_share > 0.%02d" % (i % 100) + " " * 60 for i in range(60)]  # about 100 chars a line, 6,000 in all
    text = "\n".join(lines)
    cut, tr = h.cap_playbook(text)
    got = cut.split("\n")
    want = lines[:len(got)]
    want[-1] = want[-1].rstrip()
    assert tr and len(cut) <= cap and got == want  # only whole lines survive (the last one loses its trailing blanks)
    assert len(cut) > cap - 150  # and it keeps as many as fit
    # the cap falling exactly on a newline keeps the line before it
    line = "a" * 99
    text = "\n".join([line] * 30)
    exact = "\n".join([line] * 20)  # 20 x 99 + 19 newlines = 1999 chars; the 2000th char is the next newline
    assert len(exact) == 1999
    cut, tr = h.cap_playbook(text)
    assert cut == exact and tr
    full = ("a" * 99 + "\n") * 19 + "a" * 100  # 2000 chars exactly
    assert h.cap_playbook(full + "\n" + "b" * 50) == (full, True)  # the cap fell on a newline: nothing partial to drop


def test_parse_playbook_truncates_instead_of_failing():
    cap = h.MAX_PLAYBOOK_CHARS
    pb, tr = h.parse_playbook("a <playbook>R1</playbook> b <playbook>R2</playbook>")
    assert (pb, tr) == ("R2", False)  # the last block wins
    pb, tr = h.parse_playbook("<playbook>" + "\n".join(["BOOST if q_post_trig > 30"] * 200) + "</playbook>")
    assert pb is not None and tr is True and len(pb) <= cap  # v5 refused this and failed its format bar 59 times
    for bad in ("no tags", "<playbook>   </playbook>", "<playbook>unfinished", "<playbook>a<playbook>b</playbook>", None, 3):
        assert h.parse_playbook(bad) == (None, False)


def test_cap_enforced_in_walk_forward(tmp_path):
    """A reflection that returns far more than the cap is cut by code: no failure, every stored and used playbook within the cap."""
    trig, outc = synth(20, DAYS[:5])
    rec = Recorder(long_playbook=300)
    out = h.make_out_dir(tmp_path / "r")
    counts = h.run_walk_forward(trig, outc, rec, out, arms=("L", "F"), reps=2)
    assert all(c["reflect_fail"] == 0 for k, c in counts.items() if k.startswith("L"))
    assert all(c["reflect_truncated"] == c["reflections"] > 0 for k, c in counts.items() if k.startswith("L"))
    pbs = [p.read_text() for p in (out / "playbooks").iterdir()]
    assert pbs and all(len(p.rstrip("\n")) <= h.MAX_PLAYBOOK_CHARS for p in pbs)
    sent = [pb for kind, _, pb, _ in rec.calls_log]  # the playbook text of every request, decide and reflect
    assert max(len(pb) for pb in sent) <= h.MAX_PLAYBOOK_CHARS
    logs = [json.loads(x) for x in (out / "feedback_log.jsonl").read_text().splitlines()]
    assert logs and all(g["reflect_ok"] and g["truncated"] and g["playbook_chars"] <= h.MAX_PLAYBOOK_CHARS for g in logs)


def test_cap_guard_fails_closed(tmp_path, monkeypatch):
    """If the cap were ever bypassed, the run refuses before a playbook over the cap reaches a prompt."""
    trig, outc = synth(6, DAYS[:3])
    monkeypatch.setattr(h, "cap_playbook", lambda text: (text.strip(), False))
    with pytest.raises(h.Refused, match="cap guard"):
        h.run_walk_forward(trig, outc, h.StubModel(long_playbook=300), h.make_out_dir(tmp_path / "r"), arms=("L",), reps=1)


def test_prompts_state_the_cap_and_offer_no_skip():
    d, r = h.system_decide(), h.system_reflect()
    assert "2000 characters" in r and "2500" not in r and "{cap}" not in r and "{max_rules}" not in r
    assert '"call": "boost" or "normal"' in d and "{glossary}" not in d and "{" in d
    assert "nothing is ever skipped" in d and "nothing is ever skipped" in r
    for txt in (d, r, h.PLAYBOOK_V0):
        assert "BUY" not in txt and "SKIP" not in txt and '"act"' not in txt
        assert "%" not in txt.replace("percent", "") and "11.5" not in txt and "30.7" not in txt  # no outcome statistic of the lab's days
    assert "ret_pct" not in d  # a decision prompt never mentions an outcome column
    assert "NORMAL" in h.PLAYBOOK_V0 and len(h.PLAYBOOK_V0) <= h.MAX_PLAYBOOK_CHARS


# ------------------------------------------------------------------------------------------------------------------------ arms
def test_arm_f_copies_the_night1_playbook_of_its_own_replicate_the_next_day(tmp_path):
    trig, outc = synth(30, DAYS[:6])
    rec = Recorder()
    out = h.make_out_dir(tmp_path / "r")
    counts = h.run_walk_forward(trig, outc, rec, out, arms=("L", "F"), reps=2)
    logs = [json.loads(x) for x in (out / "feedback_log.jsonl").read_text().splitlines()]
    decs = [json.loads(x) for x in (out / "decisions.jsonl").read_text().splitlines()]
    assert all(g["arm"] == "L" for g in logs)  # F never reflects
    for rep in (1, 2):
        night1 = next(g for g in logs if g["rep"] == rep and g["after_day"] == DAYS[0])
        f_pb = {d["day"]: d["playbook_sha256"] for d in decs if d["arm"] == "F" and d["rep"] == rep}
        assert f_pb[DAYS[0]] == h.sha256_text(h.PLAYBOOK_V0)  # night 1 has not happened yet on day 1
        assert set(f_pb[d] for d in DAYS[1:6]) == {night1["playbook_sha256"]}  # frozen from day 2 on, never updated
        assert night1["playbook_sha256"] != h.sha256_text(h.PLAYBOOK_V0)  # the stub did learn a rule on night 1
        l_pb = {d["day"]: d["playbook_sha256"] for d in decs if d["arm"] == "L" and d["rep"] == rep}
        assert l_pb[DAYS[1]] == f_pb[DAYS[1]]  # day 2: L and its frozen copy share the night-1 playbook
        assert len(set(l_pb.values())) > 2  # L keeps changing
        assert counts[f"F{rep}"]["frozen_copy_of"] == f"L{rep}" and counts[f"F{rep}"]["frozen_after_day"] == DAYS[0]
        assert counts[f"F{rep}"]["reflections"] == 0


def test_arm_f_never_sees_a_playbook_written_the_same_day(tmp_path):
    """Order inside a day is L then F. F must not pick up L's new playbook before the next day's 00:00Z freeze."""
    trig, outc = synth(30, DAYS[:3])
    rec = Recorder()
    h.run_walk_forward(trig, outc, rec, h.make_out_dir(tmp_path / "r"), arms=("L", "F"), reps=1)
    n1 = len([t for t in trig if t.day == DAYS[0]])
    day1_calls = [c for c in rec.calls_log if c[0] == "decide"][:2 * n1]  # day 1: L's n1 decisions, then F's n1
    assert {c[2] for c in day1_calls} == {h.PLAYBOOK_V0}


def test_arm_f_waits_for_the_first_successful_reflection(tmp_path):
    class FirstFails(h.StubModel):
        def __init__(self):
            super().__init__()
            self.n = 0

        def reflect(self, system, playbook, feedback_text):
            self.n += 1
            if self.n == 1:
                return "no playbook tags here", {"cost_usd": 0.0}
            return super().reflect(system, playbook, feedback_text)

    trig, outc = synth(30, DAYS[:5])
    out = h.make_out_dir(tmp_path / "r")
    counts = h.run_walk_forward(trig, outc, FirstFails(), out, arms=("L", "F"), reps=1)
    assert counts["L1"]["reflect_fail"] == 1 and counts["F1"]["frozen_after_day"] == DAYS[1]
    decs = [json.loads(x) for x in (out / "decisions.jsonl").read_text().splitlines()]
    f_pb = {d["day"]: d["playbook_sha256"] for d in decs if d["arm"] == "F"}
    v0 = h.sha256_text(h.PLAYBOOK_V0)
    assert f_pb[DAYS[0]] == f_pb[DAYS[1]] == v0 and f_pb[DAYS[2]] == f_pb[DAYS[3]] == f_pb[DAYS[4]] != v0


def test_arm_validation():
    trig, outc = synth(3, DAYS[:1])
    with pytest.raises(h.Refused, match="needs arm L"):
        h.run_walk_forward(trig, outc, h.StubModel(), Path("/nonexistent-never-written"), arms=("F",), reps=1)
    for code_arm in ("P", "N", "A", "R"):
        with pytest.raises(h.Refused, match="unknown LLM arm"):
            h.run_walk_forward(trig, outc, h.StubModel(), Path("/nonexistent-never-written"), arms=("L", code_arm), reps=1)
    with pytest.raises(h.Refused, match="reps"):
        h.run_walk_forward(trig, outc, h.StubModel(), Path("/nonexistent-never-written"), arms=("L",), reps=0)
    assert h.LLM_ARMS == ("L", "F") and set(h.ALL_ARMS) == {"A", "L", "F", "P", "N"}


def test_never_skips_and_a_parse_failure_falls_back_to_normal(tmp_path):
    trig, outc = synth(10, DAYS[:2])
    out = h.make_out_dir(tmp_path / "r")
    counts = h.run_walk_forward(trig, outc, h.StubModel(broken_every=5), out, arms=("L", "F"), reps=2)
    decs = [json.loads(x) for x in (out / "decisions.jsonl").read_text().splitlines()]
    assert len(decs) == 2 * 2 * len(trig)  # every arm-replicate decides every trigger: nothing is dropped
    assert all(set(d) >= {"boost", "parse_ok"} and isinstance(d["boost"], bool) and "act" not in d for d in decs)
    bad = [d for d in decs if not d["parse_ok"]]
    assert bad and all(d["boost"] is False for d in bad)  # fallback is normal, never a boost
    assert sum(c["parse_fail"] for c in counts.values()) == len(bad)


# ------------------------------------------------------------------------------------------------------------------------ walk-forward guards
def test_no_outcome_leakage_into_prompts():
    """Flip the outcomes of every trigger from day D* on. Every request up to and including D*'s decisions is identical; the first request
    that may differ is the reflection after D*. And no decision prompt ever contains an outcome column or value."""
    trig, outc = synth(30, DAYS[:8])
    dstar = DAYS[4]
    flipped = {tid: ({"flat_sol": -o["flat_sol"] * 7 + 0.123456789, "press_sol": -o["press_sol"] * 7 + 0.123456789}
                     if next(t for t in trig if t.tid == tid).day >= dstar else o) for tid, o in outc.items()}
    import tempfile

    def run(o):
        rec = Recorder()
        with tempfile.TemporaryDirectory() as td:
            h.run_walk_forward(trig, o, rec, h.make_out_dir(Path(td) / "r"), arms=("L", "F"), reps=1)
        return rec.calls_log

    a, b = run(outc), run(flipped)
    assert len(a) == len(b)
    first = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)
    n = len([t for t in trig if t.day == DAYS[0]])  # 30 a day; per day the calls are: L decides, L reflects, F decides
    didx = DAYS.index(dstar)
    assert a[first][0] == "reflect"  # decisions of day D* and before are untouched by D*'s own outcomes
    assert first == didx * (2 * n + 1) + n  # the first request that differs is L's reflection after D*'s own decisions
    assert all(x == y for x, y in zip(a[first + 1:first + 1 + n], b[first + 1:first + 1 + n]))  # F's decisions on D* (frozen) are untouched too
    assert a != b  # non-vacuous: the flip does reach later playbooks
    for kind, system, pb, user in a + b:
        if kind == "decide":
            assert "ret_pct" not in system + pb + user and "0.123456789" not in system + pb + user and "FEEDBACK" not in system + pb + user
            assert [ln.split(": ")[0] for ln in user.splitlines()[1:]] == list(h.CARD_NAMES)  # the card is the 35 decision-time fields only


def test_exact_freeze_boundary(tmp_path):
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


def test_feedback_guard_fails_closed():
    t = h.Trigger("tX", "explore-0814", h.day_start_ms(DAYS[1]) + 5, h.day_start_ms(DAYS[1]) - 1, {n: 0 for n in h.CARD_NAMES})
    st = h.ArmState("L", 1, pending=[(t, True)])
    with pytest.raises(h.Refused, match="not closed before the freeze"):
        h.feedback_rows(st, h.day_start_ms(DAYS[1]), {"tX": {"flat_sol": 0.0, "press_sol": 0.0}}, DAYS[0])


def test_playbook_guard_fails_closed(tmp_path, monkeypatch):
    trig, outc = synth(4, DAYS[:3])
    real = h.feedback_rows

    def leaky(st, freeze_ms, outcomes, day):
        rows, n, _ = real(st, freeze_ms, outcomes, day)
        return rows, n, freeze_ms  # pretend a row known AT the freeze was used
    monkeypatch.setattr(h, "feedback_rows", leaky)
    with pytest.raises(h.Refused, match="playbook built from an outcome known after the day start"):
        h.run_walk_forward(trig, outc, h.StubModel(), h.make_out_dir(tmp_path / "r"), arms=("L",), reps=1)


def test_feedback_sampling_cap_is_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "MAX_FEEDBACK_ROWS", 5)
    trig, outc = synth(12, DAYS[:2])
    rec = Recorder()
    counts = h.run_walk_forward(trig, outc, rec, h.make_out_dir(tmp_path / "r"), arms=("L",), reps=1)
    fb = next(c[3] for c in rec.calls_log if c[0] == "reflect")
    assert len(fb.splitlines()) == 2 + 5 + 1 and counts["L1"]["feedback_sampled_out"] == 7
    assert fb.splitlines()[1].startswith("call,") and fb.splitlines()[-1].startswith("SUMMARY rows=5 (sampled from 12); BOOST n=")


# ------------------------------------------------------------------------------------------------------------------------ arm P: placebo
def test_placebo_sets_are_deterministic_exact_k_and_outcome_free():
    s = h.placebo_boost_sets(40, 7, "v6|P|1|2026-08-20", 200)
    assert s.shape == (200, 40) and (s.sum(axis=1) == 7).all()
    assert np.array_equal(s, h.placebo_boost_sets(40, 7, "v6|P|1|2026-08-20", 200))  # deterministic
    assert not np.array_equal(s, h.placebo_boost_sets(40, 7, "v6|P|2|2026-08-20", 200))  # the seed is (rep, day)
    assert not np.array_equal(s, h.placebo_boost_sets(40, 7, "v6|P|1|2026-08-21", 200))
    assert h.placebo_seed(2, "2026-08-21") == "v6|P|2|2026-08-21"
    assert h.placebo_boost_sets(10, 0, "z", 5).sum() == 0 and (h.placebo_boost_sets(10, 99, "z", 5).sum(axis=1) == 10).all()  # k clamped
    assert h.placebo_boost_sets(0, 3, "z", 5).shape == (5, 0)
    # uniform placement: each trigger is boosted with frequency about k/n
    big = h.placebo_boost_sets(40, 10, "u", 4000)
    assert abs(big.mean() - 0.25) < 1e-12 and np.abs(big.mean(axis=0) - 0.25).max() < 0.04
    # no outcome can reach the selection: the function has no outcome parameter, and no hidden global state
    assert list(inspect.signature(h.placebo_boost_sets).parameters) == ["n", "k", "seed_text", "draws"]


def test_placebo_selection_in_score_does_not_depend_on_outcomes(tmp_path, monkeypatch):
    trig, outc = synth(45)
    run = tmp_path / "run"
    write_decisions(run, trig, lambda arm, rep, t: arm == "L" and int(t.tid[-3:]) % 4 == 0)
    calls = []
    real = h.placebo_boost_sets
    monkeypatch.setattr(h, "placebo_boost_sets", lambda *a, **k: (calls.append((a, tuple(sorted(k.items())))), real(*a, **k))[1])
    h.score(run, trig, outc, GOOD_COUNTS, placebo_draws=50)
    first = list(calls)
    calls.clear()
    flipped = {tid: {"flat_sol": -o["flat_sol"] + 0.5, "press_sol": o["press_sol"] * 3} for tid, o in outc.items()}
    h.score(run, trig, flipped, GOOD_COUNTS, placebo_draws=50)
    assert calls == first and len(first) == 3 * (len(DAYS) - h.BURN_IN_DAYS)  # one draw set per replicate and test day, same arguments
    assert all(a[2] == f"v6|P|{rep}|{day}" for (a, _), (rep, day) in zip(first, [(r, d) for r in (1, 2, 3) for d in DAYS[h.BURN_IN_DAYS:]]))


def test_placebo_mean_matches_the_analytic_expectation(tmp_path):
    """P's expected extra on a day is k/n x the day's outcome sum. The seeded Monte Carlo must agree within 4 standard errors."""
    trig, outc = synth(45)
    run = tmp_path / "run"
    write_decisions(run, trig, lambda arm, rep, t: arm == "L" and (int(t.tid[-3:]) + rep) % 5 == 0)
    draws = 1000
    s = h.score(run, trig, outc, GOOD_COUNTS, placebo_draws=draws)
    ts_by_day = dict(h.by_day(trig))
    test_days = DAYS[h.BURN_IN_DAYS:]
    mc = s["models"]["flat_sol"]["totals_sol_at_stake"]["P_mean"] - s["models"]["flat_sol"]["totals_sol_at_stake"]["A"]
    exp, var = 0.0, 0.0
    for rep in (1, 2, 3):
        for d in test_days:
            o = np.array([outc[t.tid]["flat_sol"] for t in ts_by_day[d]])
            n = len(o)
            k = sum(1 for t in ts_by_day[d] if (int(t.tid[-3:]) + rep) % 5 == 0)
            exp += k / n * o.sum() / 3
            var += (k * o.var() * (n - k) / (n - 1)) / 9 / draws  # sampling without replacement, mean over 3 replicates
    assert abs(mc - exp) < 4 * var ** 0.5


# ------------------------------------------------------------------------------------------------------------------------ arm N: numeric tilt
def test_numeric_tilt_uses_only_past_outcomes():
    trig, outc = synth(80, DAYS[:7])
    days = h.by_day(trig)
    target = days[5][0]
    s1 = h.numeric_tilt_scores(days, outc, {target})
    assert s1[target] is not None  # 5 x 80 = 400 fit rows >= N_MIN_FIT: the test is not vacuous
    outc2 = {tid: (dict(o, flat_sol=-o["flat_sol"] * 50) if next(t for t in trig if t.tid == tid).day >= target else o) for tid, o in outc.items()}
    s2 = h.numeric_tilt_scores(days, outc2, {target})
    assert np.array_equal(s1[target], s2[target])  # flipping day D's (and later) outcomes cannot change D's scores
    early = h.numeric_tilt_scores(days, outc, {days[0][0], days[1][0], days[2][0]})
    assert early[days[0][0]] is None and early[days[2][0]] is None  # fewer than N_MIN_FIT fit rows: nothing boosted
    assert h.N_MIN_FIT == 300 and h.N_LABEL_PCT == 90.0 and h.N_LAMBDA == 1.0


def test_numeric_tilt_fit_boundary_bites():
    """150 D-1 triggers whose outcomes land after D 00:00Z cannot move D's scores; the same flip on rows known 1 ms before D 00:00Z does."""
    rng = random.Random(3)
    d0, d1, d2 = DAYS[0], DAYS[1], DAYS[2]
    trig, outc = [], {}

    def add(tid, day, ms, known, field_value, flat):
        card = make_card(random.Random(tid))
        card["top3_seller_share"] = field_value
        trig.append(h.Trigger(tid, blk_of(day), ms, known, card))
        outc[tid] = {"flat_sol": flat, "press_sol": flat}

    for i in range(320):  # already >= N_MIN_FIT without the late rows, so "known after midnight" is a real fit, not a skipped day
        x = rng.uniform(0, 1)
        ms = h.day_start_ms(d0) + 60_000 + i * 250_000
        add(f"a{i:03d}", d0, ms, ms + h.OUTCOME_KNOWN_AFTER_MS, x, 0.3 if x > 0.8 else -0.01)
    late = []
    for i in range(150):
        ms = h.day_start_ms(d2) - 899_000 + i * 5_000  # 23:45:01Z .. 23:57:26Z on d1
        late.append((f"b{i:03d}", ms, rng.uniform(0, 1)))
    for i in range(40):
        ms = h.day_start_ms(d2) + 60_000 + i * 60_000
        add(f"c{i:03d}", d2, ms, ms + h.OUTCOME_KNOWN_AFTER_MS, rng.uniform(0, 1), 0.0)

    def scores(known_after_midnight: bool, flipped: bool):
        t2, o2 = list(trig), dict(outc)
        for tid, ms, x in late:
            known = ms + h.OUTCOME_KNOWN_AFTER_MS if known_after_midnight else h.day_start_ms(d2) - 1
            card = make_card(random.Random(tid))
            card["top3_seller_share"] = x
            t2.append(h.Trigger(tid, blk_of(d1), ms, known, card))
            flat = (0.3 if x > 0.8 else -0.01) if not flipped else (-0.01 if x > 0.8 else 0.3)
            o2[tid] = {"flat_sol": flat, "press_sol": flat}
        t2.sort(key=lambda t: (t.decision_ms, t.tid))
        return h.numeric_tilt_scores(h.by_day(t2), o2, {d2})[d2]

    assert all(ms + h.OUTCOME_KNOWN_AFTER_MS > h.day_start_ms(d2) for _, ms, _ in late)
    assert scores(True, False) is not None and scores(False, False) is not None  
    assert np.array_equal(scores(True, False), scores(True, True))
    assert not np.array_equal(scores(False, False), scores(False, True))


def test_numeric_tilt_learns_a_planted_top_decile_field_and_matches_L_counts(tmp_path):
    trig, outc = synth(150, DAYS[:5])  # all before the planted shift: top3_seller_share > 0.8 is the tail
    days = h.by_day(trig)
    sc = h.numeric_tilt_scores(days, outc, {days[4][0]})[days[4][0]]
    ts = days[4][1]
    top = np.argsort(-sc)[:30]  # the 30 highest-scored triggers of the day (a fifth)
    assert np.mean([ts[i].card["top3_seller_share"] > 0.7 for i in top]) >= 0.7  # 0.3 by chance
    # in score(): N boosts exactly L's count (per replicate, mean over replicates) on every test day that has >= N_MIN_FIT fit rows
    trig, outc = synth(45)
    run = tmp_path / "run"
    pol = lambda arm, rep, t: arm == "L" and (int(t.tid[-3:]) * rep) % 6 == 0  # noqa: E731
    write_decisions(run, trig, pol)
    s = h.score(run, trig, outc, GOOD_COUNTS, placebo_draws=20)
    c = s["boost_counts_mean_over_reps_test"]
    ts_by_day = dict(h.by_day(trig))
    want_n = 0.0
    for j, d in enumerate(DAYS):
        if j >= h.BURN_IN_DAYS and 45 * j >= h.N_MIN_FIT:  # 45 triggers a day, all closed before the next midnight
            want_n += sum(sum(1 for t in ts_by_day[d] if pol("L", r, t)) for r in (1, 2, 3)) / 3
    assert abs(c["N"] - want_n) < 1e-9 and 0 < c["N"] < c["L"] and abs(c["P"] - c["L"]) < 1e-9 and c["F"] == 0


def test_numeric_tilt_equals_sklearn_logistic_on_the_rank_features():
    """Lane A's model is sklearn LogisticRegression(C=1.0, lbfgs) on mid-rank features; the numpy IRLS here is the same objective."""
    lm = pytest.importorskip("sklearn.linear_model")
    rng = np.random.default_rng(5)
    X = rng.lognormal(size=(400, 6))  # heavy tails, as SOL amounts are
    X[rng.random(X.shape) < 0.05] = np.nan
    y = (rng.random(400) < 0.1 + 0.3 * (np.nan_to_num(X[:, 0]) > 2)).astype(float)
    Z = h.rank_features(X, X)
    assert np.isfinite(Z).all() and Z.min() >= -0.5 and Z.max() <= 0.5
    w = h.fit_logistic_l2(Z, y)
    ref = lm.LogisticRegression(C=1.0, solver="lbfgs", max_iter=5000, tol=1e-12).fit(Z, y)
    new = rng.lognormal(size=(50, 6))
    s_mine = w[0] + h.rank_features(X, new) @ w[1:]
    s_ref = ref.decision_function(h.rank_features(X, new))
    assert np.max(np.abs(s_mine - s_ref)) < 1e-4
    assert h.fit_logistic_l2(Z, np.zeros(400)) is None  # one label class
    # mid-rank by hand: ties share the mid-rank, an unseen value sits at its insertion rank, a missing value maps to 0
    tr = np.array([[1.0], [2.0], [2.0], [4.0]])
    assert h.rank_features(tr, np.array([[2.0], [0.0], [9.0], [np.nan]])).ravel().tolist() == [0.0, -0.5, 0.5, 0.0]


def test_haircut_report_only_block(tmp_path):
    assert h.haircut_sol({"q_post_trig": 40.0}) == pytest.approx(4 * 0.01 / 40.0)
    assert h.haircut_sol({"q_post_trig": None}) == pytest.approx(4 * 0.01 / h.V_FLOOR_SOL)  # missing: the largest haircut
    assert h.haircut_sol({"q_post_trig": -3}) == h.haircut_sol({})
    trig, outc = synth(45)
    run = tmp_path / "run"
    write_decisions(run, trig, lambda a, r, t: oracle(a, r, t, outc))
    s = h.score(run, trig, outc, GOOD_COUNTS, placebo_draws=50)
    for mk in ("flat_sol", "press_sol"):
        m = s["models"][mk]
        hb = m["haircut_lane_a_formula_report_only"]
        k = s["boost_counts_mean_over_reps_test"]["L"]
        assert 0.01 / 40 <= hb["mean_haircut_per_boost_sol"] <= 0.04 / 17.6  # q is 18..40 in the synthetic cards
        assert hb["L_minus_A_total_after_haircut"] == pytest.approx(m["contrasts"]["L-A"]["total"] - k * hb["mean_haircut_per_boost_sol"])
        assert "NOT repriced" in hb["source"]
        assert m["breakeven_extra_cost_pct_of_extra_stake_report_only"] == pytest.approx(100 * m["contrasts"]["L-A"]["total"] / (k * 0.1))


# ------------------------------------------------------------------------------------------------------------------------ scoring
def test_contrast6_by_hand():
    trig, _ = synth(2, DAYS[:2])
    ts_by_day = dict(h.by_day(trig))
    days = DAYS[:2]
    m_x = {t.tid: 1.0 for t in trig}
    m_y = dict(m_x)
    out = {t.tid: 0.0 for t in trig}
    tids = [[t.tid for t in ts_by_day[d]] for d in days]
    for tid, v in zip(tids[0] + tids[1], (0.5, -0.1, 0.2, 0.3)):
        out[tid] = v
    m_x[tids[0][0]], m_x[tids[1][1]] = 2.0, 2.0  # X boosts the +0.5 and the +0.3 trade; Y boosts nothing
    c = h.contrast6(m_x, m_y, out, ts_by_day, days)
    assert c["total"] == pytest.approx(0.8) and c["mean_daily"] == pytest.approx(0.4) and c["days_positive"] == 2
    assert c["ex_best_day"] == pytest.approx(0.3)  # day 1 contributes 0.5, day 2 0.3
    assert c["ex_top3"] == 0.0  # four trades: removing the three largest contributions (0.5, 0.3, 0 or 0) leaves one zero
    assert c["late_half"] == pytest.approx(0.3)
    # y boosts the same trades: zero contrast, and the sign of a loss is kept
    assert h.contrast6(m_x, m_x, out, ts_by_day, days)["total"] == 0.0
    assert h.contrast6(m_y, m_x, out, ts_by_day, days)["total"] == pytest.approx(-0.8)


def oracle(arm, rep, t, outc):
    return arm == "L" and outc[t.tid]["flat_sol"] >= TAIL


def test_end_to_end_stub_run_and_score_pass_on_a_planted_tail(tmp_path):
    trig, outc = synth(45)
    tp, op, run = stub_run(tmp_path, trig, outc, reps="3")
    assert h.main(["score", "--run-dir", str(run), "--triggers", str(tp), "--outcomes", str(op), "--out", str(tmp_path / "score.json")]) == 0
    s = json.loads((tmp_path / "score.json").read_text())
    assert s["test_days"] == len(DAYS) - h.BURN_IN_DAYS and s["boost_mult"] == 2.0 and s["o1_verdict_eligible"] is False
    assert set(s["models"]) == {"flat_sol", "press_sol"} and s["models"]["flat_sol"]["L_rep1_sized_book_in_sample"]["label"] == "in-sample, report-only"
    # the stub learns the planted tail from true outcomes, re-learns after the shift, and its frozen night-1 copy goes stale
    for mk in ("flat_sol", "press_sol"):
        c = s["models"][mk]["contrasts"]
        assert c["L-A"]["total"] > 0 and c["L-P"]["total"] > 0 and c["L-F"]["total"] > 0 and c["L-N"]["total"] >= 0
    assert s["verdict"] == "PASS_OFFLINE", s["failed_bars"]
    assert all(s["bars"].values()) and len(s["bars"]) == 2 + 6 * 2
    assert max(s["L_boost_share_test_by_rep"].values()) <= h.MAX_BOOST_SHARE
    with pytest.raises(FileExistsError):
        h.write_once_json(tmp_path / "score.json", {})


def _score(tmp_path, policy, outc=None, trig=None, counts=GOOD_COUNTS, parse_ok=lambda arm, rep, t: True, name="s"):
    trig = trig or synth(45)[0]
    outc = outc or synth(45)[1]
    run = tmp_path / name
    write_decisions(run, trig, policy, parse_ok=parse_ok)
    return h.score(run, trig, outc, counts, placebo_draws=200)


def test_oracle_baseline_passes_and_every_bar_can_fail(tmp_path):
    trig, outc = synth(45)
    ok = _score(tmp_path, lambda a, r, t: oracle(a, r, t, outc), outc, trig, name="ok")
    assert ok["verdict"] == "PASS_OFFLINE", ok["failed_bars"]

    def failed(res):
        return set(res["failed_bars"])

    # B2: boosting everything is a 2x book, not a spotter
    allb = _score(tmp_path, lambda a, r, t: a == "L", outc, trig, name="all")
    assert "B2_boost_share_le_30pct" in failed(allb) and allb["L_boost_share_test_by_rep"][1] == 1.0
    # B3 and B4: boosting the planted losers loses to base and to random
    inv = _score(tmp_path, lambda a, r, t: a == "L" and outc[t.tid]["flat_sol"] < TAIL and int(t.tid[-3:]) % 4 == 0, outc, trig, name="inv")
    assert {"B3_L_beats_base[flat_sol]", "B4_L_beats_random_placebo[flat_sol]"} <= failed(inv)
    # B4: boosting at random is no better than the random placebo (and the bootstrap lower bound is below zero)
    rnd = _score(tmp_path, lambda a, r, t: a == "L" and random.Random(f"{r}{t.tid}").random() < 0.2, outc, trig, name="rnd")
    assert "B4_L_beats_random_placebo[flat_sol]" in failed(rnd) and rnd["models"]["flat_sol"]["contrasts"]["L-P"]["boot_lo5"] <= 0
    # B5: a frozen copy that is as good as the learner is not beaten
    fro = _score(tmp_path, lambda a, r, t: oracle("L", r, t, outc), outc, trig, name="fro")
    assert "B5_L_beats_frozen_night1[flat_sol]" in failed(fro) and fro["models"]["flat_sol"]["contrasts"]["L-F"]["total"] == 0.0
    # B8: a gain that is all in the first half of the test days fades
    fade = _score(tmp_path, lambda a, r, t: a == "L" and ((outc[t.tid]["flat_sol"] >= TAIL) if t.day < DAYS[14] else
                                                          (outc[t.tid]["flat_sol"] < TAIL and int(t.tid[-3:]) % 3 == 0)), outc, trig, name="fade")
    assert "B8_late_half_gain[flat_sol]" in failed(fade) and fade["models"]["flat_sol"]["contrasts"]["L-A"]["late_half"] < 0


def test_b6_numeric_tilt_beats_a_weak_learner(tmp_path):
    """B6 bites: L boosts 60 weakly chosen triggers per... the numeric tilt at the same count does better, so L - N < 0 and B6 fails."""
    trig, outc = synth(45)
    ts_by_day = dict(h.by_day(trig))
    pick = {}
    for d in DAYS[h.BURN_IN_DAYS:]:
        losers = [t.tid for t in ts_by_day[d] if outc[t.tid]["flat_sol"] < TAIL][:6]
        pick.update({tid: True for tid in losers})  # L boosts six non-winners a day, N (past-trained, top decile) hits more tails
    res = _score(tmp_path, lambda a, r, t: a == "L" and pick.get(t.tid, False), outc, trig, name="b6")
    assert res["models"]["flat_sol"]["contrasts"]["L-N"]["total"] < 0
    assert "B6_L_not_below_numeric_matched[flat_sol]" in res["failed_bars"] and "B6_L_not_below_numeric_matched[press_sol]" in res["failed_bars"]


def test_b7_concentration_in_a_few_trades_fails_ex_top3_and_ex_best_day(tmp_path):
    trig, outc = synth(45)
    ts_by_day = dict(h.by_day(trig))
    test_days = DAYS[h.BURN_IN_DAYS:]
    outc = {k: dict(v) for k, v in outc.items()}
    big = [ts_by_day[test_days[i]][0].tid for i in (2, 9, 15)]  # three monster winners
    for tid in big:
        outc[tid] = {"flat_sol": 3.0, "press_sol": 2.7}
    # L boosts the three monsters and, in addition, 60 plain triggers whose boost loses a little
    plain = [t.tid for d in test_days for t in ts_by_day[d][1:4] if t.tid not in big and outc[t.tid]["flat_sol"] < TAIL][:60]
    res = _score(tmp_path, lambda a, r, t: a == "L" and (t.tid in big or t.tid in plain), outc, trig, name="conc")
    c = res["models"]["flat_sol"]["contrasts"]["L-A"]
    assert c["total"] > 0 and c["ex_top3"] < 0  # the book lives on three trades
    assert "B7_ex_best_day_and_ex_top3[flat_sol]" in res["failed_bars"] and "B7_ex_best_day_and_ex_top3[press_sol]" in res["failed_bars"]


def test_b1_format_bar_parse_and_reflection(tmp_path):
    trig, outc = synth(45)
    pol = lambda a, r, t: oracle(a, r, t, outc)  # noqa: E731
    # parse rate 0.98 passes, just under fails; applies to F replicates too
    ids = {t.tid: i for i, t in enumerate(trig)}
    n = len(trig)
    just_ok = _score(tmp_path, pol, outc, trig, name="p1", parse_ok=lambda a, r, t: ids[t.tid] >= int(0.02 * n))
    assert "B1_parse_and_reflection" not in just_ok["failed_bars"] and min(just_ok["parse_rate"].values()) >= h.PARSE_MIN
    under = _score(tmp_path, pol, outc, trig, name="p2", parse_ok=lambda a, r, t: not (a == "F" and r == 2 and ids[t.tid] < int(0.03 * n)))
    assert "B1_parse_and_reflection" in under["failed_bars"] and under["parse_rate"]["F2"] < h.PARSE_MIN
    # reflection success 0.95 passes (1 of 20), 0.90 fails (2 of 20); missing counts fail closed
    ok = _score(tmp_path, pol, outc, trig, name="r1", counts={"L1": {"reflections": 20, "reflect_fail": 1}})
    assert "B1_parse_and_reflection" not in ok["failed_bars"] and ok["reflect_ok_rate"]["L1"] == pytest.approx(0.95)
    bad = _score(tmp_path, pol, outc, trig, name="r2", counts={"L1": {"reflections": 20, "reflect_fail": 2}})
    assert "B1_parse_and_reflection" in bad["failed_bars"]
    assert "B1_parse_and_reflection" in _score(tmp_path, pol, outc, trig, name="r3", counts=None)["failed_bars"]
    assert (h.PARSE_MIN, h.REFLECT_OK_MIN, h.MAX_BOOST_SHARE) == (0.98, 0.95, 0.30)


def test_both_fail_models_must_hold(tmp_path):
    """Planted tails pay under flat but lose under press: only the [press_sol] bars fail."""
    trig, outc = synth(45)
    outc = {k: ({"flat_sol": v["flat_sol"], "press_sol": -0.02} if v["flat_sol"] >= TAIL else dict(v)) for k, v in outc.items()}
    res = _score(tmp_path, lambda a, r, t: oracle(a, r, t, outc), outc, trig, name="press")
    assert res["verdict"] == "KILL_OFFLINE"
    assert res["failed_bars"] and all(b.endswith("[press_sol]") for b in res["failed_bars"])
    assert {"B3_L_beats_base[press_sol]", "B4_L_beats_random_placebo[press_sol]"} <= set(res["failed_bars"])
    assert res["bars"]["B3_L_beats_base[flat_sol]"] is True


def test_not_decidable_cases(tmp_path):
    trig, outc = synth(5, DAYS[:6])
    run = tmp_path / "small"
    write_decisions(run, trig, lambda a, r, t: a == "L")
    assert h.score(run, trig, outc, GOOD_COUNTS)["verdict"] == "NOT_DECIDABLE"  # 3 test days
    trig, outc = synth(45)
    zero = _score(tmp_path, lambda a, r, t: False, outc, trig, name="zero")
    assert zero["verdict"] == "NOT_DECIDABLE" and "too few to read" in zero["why"]
    few = _score(tmp_path, lambda a, r, t: a == "L" and int(t.tid[-3:]) == 7 and t.day > DAYS[3] and t.day < DAYS[13], outc, trig, name="few")
    assert few["verdict"] == "NOT_DECIDABLE" and h.MIN_TEST_BOOSTS == 50
    only_l = tmp_path / "onlyl"
    write_decisions(only_l, trig, lambda a, r, t: True)
    lines = [ln for ln in (only_l / "decisions.jsonl").read_text().splitlines() if '"arm": "L"' in ln]
    (only_l / "decisions.jsonl").write_text("\n".join(lines) + "\n")
    assert h.score(only_l, trig, outc, GOOD_COUNTS)["verdict"] == "NOT_DECIDABLE"


def test_score_refuses_missing_decisions_changed_inputs_and_other_sha(tmp_path, monkeypatch):
    trig, outc = synth(45)
    tp, op, run = stub_run(tmp_path, trig, outc, reps="1")
    args = ["score", "--run-dir", str(run), "--triggers", str(tp), "--outcomes", str(op)]
    real_v6, real_v5 = h.harness_sha256(), h.v5_harness_sha256()
    monkeypatch.setattr(h, "harness_sha256", lambda: "0" * 64)
    assert h.main(args + ["--out", str(tmp_path / "s1.json")]) == 3 and not (tmp_path / "s1.json").exists()
    monkeypatch.setattr(h, "harness_sha256", lambda: real_v6)
    monkeypatch.setattr(h, "v5_harness_sha256", lambda: "1" * 64)  # the v5 file v6 imports changed: the registration is void
    assert h.main(args + ["--out", str(tmp_path / "s2.json")]) == 3 and not (tmp_path / "s2.json").exists()
    monkeypatch.setattr(h, "v5_harness_sha256", lambda: real_v5)
    assert h.main(args + ["--out", str(tmp_path / "s3.json")]) == 0
    s = json.loads((tmp_path / "s3.json").read_text())
    assert s["run"]["harness_sha256_run"] == s["run"]["harness_sha256_score"] == real_v6 and s["run"]["v5_harness_sha256_run"] == real_v5
    assert s["run"]["model"] == "stub" and s["run"]["reps"] == 1 and s["run"]["outcome_basis"] == "el2"
    lines = (run / "decisions.jsonl").read_text().splitlines()
    (run / "decisions.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    assert h.main(args + ["--out", str(tmp_path / "s4.json")]) == 3  # a missing decision is never a silent normal
    with open(op, "a") as f:
        f.write("\n")
    assert h.main(args + ["--out", str(tmp_path / "s5.json")]) == 3  # any byte change: sha256 differs from the manifest
    assert h.o1_eligible({"model": "haiku", "reps": 3, "max_days": 0, "outcome_basis": "el2", "arms": ["L", "F"]})
    assert not h.o1_eligible({"model": "haiku", "reps": 3, "max_days": 3, "outcome_basis": "el2", "arms": ["L", "F"]})
    assert not h.o1_eligible({"model": "haiku", "reps": 1, "max_days": 0, "outcome_basis": "el2", "arms": ["L", "F"]})
    assert not h.o1_eligible({"model": "stub", "reps": 3, "max_days": 0, "outcome_basis": "el2", "arms": ["L", "F"]})


def test_score_refuses_a_non_el2_basis(tmp_path):
    trig, outc = synth(45)
    tp, op, run = stub_run(tmp_path, trig, outc, basis="frozen")
    assert h.main(["score", "--run-dir", str(run), "--triggers", str(tp), "--outcomes", str(op), "--out", str(tmp_path / "s.json")]) == 3


def test_decisions_must_be_booleans(tmp_path):
    trig, outc = synth(45)
    run = tmp_path / "run"
    write_decisions(run, trig, lambda a, r, t: a == "L")
    rows = [json.loads(x) for x in (run / "decisions.jsonl").read_text().splitlines()]
    rows[0]["boost"] = "yes"
    (run / "decisions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with pytest.raises(h.Refused, match="booleans"):
        h.score(run, trig, outc, GOOD_COUNTS)


# ------------------------------------------------------------------------------------------------------------------------ CLI, spend caps, dry
def test_budget_caps_pilot_1_and_ledger_8(tmp_path, monkeypatch):
    assert (h.PILOT_BUDGET_MAX_USD, h.LEDGER_BUDGET_MAX_USD) == (1.0, 8.0)
    h.check_budget(8.0, 0)
    h.check_budget(1.0, 3)
    h.check_budget(0.0, 0)
    for bad, md in ((8.01, 0), (16.0, 0), (1.01, 3), (5.0, 3), (-1.0, 0), (float("nan"), 0), (float("inf"), 0), (None, 0)):
        with pytest.raises(h.Refused):
            h.check_budget(bad, md)
    trig, outc = synth(3, DAYS[:1])
    tp, op = write_inputs(tmp_path / "in", trig, outc)

    def boom(*a, **k):
        raise AssertionError("HaikuModel constructed")

    monkeypatch.setattr(h, "HaikuModel", boom)
    base = ["run", "--triggers", str(tp), "--outcomes", str(op), "--model", "haiku"]
    assert h.main(base + ["--out-dir", str(tmp_path / "a")]) == 3  # no --live / --approval
    full = base + ["--live", "--approval", "n_test", "--budget-usd", "1", "--ledger", str(tmp_path / "l.jsonl"), "--save-dir", str(tmp_path / "s")]
    assert h.main(full + ["--out-dir", str(tmp_path / "b")]) == 3  # no --expect-sha256
    assert h.main(full + ["--out-dir", str(tmp_path / "c"), "--expect-sha256", f"triggers={h.sha256_file(tp)},outcomes=0"]) == 3
    assert h.main(["run", "--triggers", str(tp), "--outcomes", str(op), "--model", "stub", "--live", "--out-dir", str(tmp_path / "d")]) == 3
    pins = ["--expect-sha256", f"triggers={h.sha256_file(tp)},outcomes={h.sha256_file(op)}"]
    over = [x if x != "1" else "8.01" for x in full]
    assert h.main(over + pins + ["--out-dir", str(tmp_path / "e")]) == 3  # above the coded ledger ceiling
    pilot = [x if x != "1" else "1.01" for x in full]
    assert h.main(pilot + pins + ["--max-days", "1", "--out-dir", str(tmp_path / "f")]) == 3  # above the coded pilot ceiling
    in2 = tmp_path / "fz"
    tp2, op2 = write_inputs(in2, trig, outc, basis="frozen")
    full2 = [str(tp2) if x == str(tp) else str(op2) if x == str(op) else x for x in full]
    assert h.main(full2 + ["--expect-sha256", f"triggers={h.sha256_file(tp2)},outcomes={h.sha256_file(op2)}", "--out-dir", str(tmp_path / "g")]) == 3
    assert not any((tmp_path / x).exists() for x in "abcdefg")  # nothing written, no caller built, by any refusal


def test_haiku_model_request_shape():
    class FakeCaller:
        def __init__(self):
            self.calls = []

        def call(self, **kw):
            self.calls.append(kw)

            class R:
                text, cost_usd, request_sha256, replayed, usage = '{"call":"normal","why":"x"}', 0.0, "s", False, {}
            return R()

    m = h.HaikuModel.__new__(h.HaikuModel)
    m.caller = FakeCaller()
    m.decide("SYS", "PB", "CARD")
    m.reflect("SYS", "PB", "FB")
    d, r = m.caller.calls
    assert "thinking" not in d and d["max_tokens"] == h.DECIDE_MAX_TOKENS == 400  # caller default: thinking disabled
    assert r["thinking"] is None and r["max_tokens"] == h.REFLECT_MAX_TOKENS == 10_000  # omitted: adaptive
    assert d["system"][0]["cache_control"] == {"type": "ephemeral"} and d["system"][0]["text"].endswith("PLAYBOOK:\nPB")
    assert h.REFLECT_MAX_TOKENS / 208 + 5 < 60  # inside haiku_caller's 60 s attempt timeout at the slowest measured speed (v5 P1)


def test_dry_mode_reads_no_outcome_and_bounds_cost(tmp_path, monkeypatch):
    trig, outc = synth(10, DAYS[:4])
    tp, _ = write_inputs(tmp_path / "in", trig, outc)

    def boom(*a, **k):
        raise AssertionError("dry mode opened outcomes")

    monkeypatch.setattr(h, "load_outcomes", boom)
    assert h.main(["dry", "--triggers", str(tp), "--out-dir", str(tmp_path / "dry"), "--reps", "2"]) == 0
    d = json.loads((tmp_path / "dry" / "dry.json").read_text())
    c = d["cost"]
    assert c["calls"]["decide"]["n"] == 2 * 2 * len(trig) and c["calls"]["reflect"]["n"] == 2 * 3  # L and F decide; only L reflects
    assert 0 < c["expected_usd"] < c["worst_case_usd"] and c["max_body_bytes"] < 100_000
    assert c["expected_at_v5_o1_measured_usd"] > 0 and d["ceilings_usd"] == {"ledger": 8.0, "pilot": 1.0}


def test_caller_stop_exits_4_and_keeps_partial_output(tmp_path, monkeypatch):
    from tools.haiku_caller import BudgetExceeded

    trig, outc = synth(4, DAYS[:2])
    tp, op = write_inputs(tmp_path / "in", trig, outc)

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
    assert not (tmp_path / "r" / "manifest.json").exists() and (tmp_path / "r" / "run_start.json").exists()


def test_run_lock_refuses_a_second_process(tmp_path, monkeypatch):
    import fcntl

    trig, outc = synth(3, DAYS[:1])
    tp, op = write_inputs(tmp_path / "in", trig, outc)
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
    assert h.RUN_LOCK_SUFFIX == ".v6-run.lock"
    with h.run_lock(str(ledger)):
        with pytest.raises(h.Refused, match="another v6 run"):
            with h.run_lock(str(ledger)):
                pass


def test_run_start_written_before_calls_and_pins_both_shas(tmp_path):
    trig, outc = synth(3, DAYS[:2])
    _, _, run = stub_run(tmp_path, trig, outc, reps="1")
    st = json.loads((run / "run_start.json").read_text())
    assert st["harness_sha256"] == h.harness_sha256() and st["v5_harness_sha256"] == h.v5_harness_sha256()
    assert st["prompt_sha256"] == h.PROMPT_SHAS and st["outcome_basis"] == "el2" and st["args"]["reps"] == 1


def test_pilot_structure_adds_decision_structure_without_outcomes(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    rows = [{"arm": "L", "rep": 1, "tid": f"t{i}", "boost": i % 4 == 0, "parse_ok": i != 1, "request_sha256": f"d{i}"} for i in range(8)]
    (run / "decisions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (run / "feedback_log.jsonl").write_text(json.dumps({"reflect_ok": True, "truncated": True, "playbook_chars": 1987, "request_sha256": "r0"}) + "\n")
    led = []
    for i in range(8):
        led.append({"kind": "reserve", "reservation_id": f"rd{i}", "request_sha256": f"d{i}", "cost_usd": 0.01})
        led.append({"kind": "settle", "reservation_id": f"rd{i}", "request_sha256": f"d{i}", "http_status": 200, "input_tokens": 100,
                    "output_tokens": 20, "latency_ms": 800, "stop_reason": "end_turn", "cost_usd": -0.009})
    led.append({"kind": "reserve", "reservation_id": "rr0", "request_sha256": "r0", "cost_usd": 0.02})
    led.append({"kind": "settle", "reservation_id": "rr0", "request_sha256": "r0", "http_status": 200, "input_tokens": 5000, "output_tokens": 900,
                "latency_ms": 20000, "stop_reason": "end_turn", "cost_usd": -0.015})
    (tmp_path / "ledger.jsonl").write_text("\n".join(json.dumps(r) for r in led) + "\n")
    doc = h.pilot_structure(run, tmp_path / "ledger.jsonl")
    assert doc["matches_ledger_summary"] is True and doc["parse_ok_rate"] == 7 / 8 and doc["reflect_ok_rate"] == 1.0
    assert doc["v6_decision_structure"]["L1"] == {"decisions": 8, "boosts": 2, "parse_fail": 1, "boost_share": 0.25}
    assert doc["v6_reflection_structure"] == {"reflections": 1, "truncated": 1, "max_playbook_chars": 1987}
    assert not any("flat" in k or "press" in k or "ret_pct" in k for k in doc)


def test_seals_are_inherited_from_v5(tmp_path):
    assert h.ALLOWED_BLOCKS is h.v5.ALLOWED_BLOCKS and h.NO_DATA_FROM_MS == 1790812800000  # 2026-10-01T00Z
    trig, outc = synth(2, DAYS[:2])
    tp, _ = write_inputs(tmp_path / "in", trig, outc)
    sealed = tmp_path / "x-canary-y"
    sealed.mkdir()
    (sealed / "t.jsonl").write_text(tp.read_text())
    with pytest.raises(h.Refused, match="forbidden fragment"):
        h.load_triggers(sealed / "t.jsonl")
    rows = [json.loads(x) for x in tp.read_text().splitlines()]
    rows[0]["outcome_known_ms"] += 1  # never trust the file's outcome_known_ms
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with pytest.raises(h.Refused, match="exactly decision_ms"):
        h.load_triggers(bad)


def test_prereg_document_pins_the_code_constants():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "research" / "haiku-v6" / "PREREG.md").read_text()
    for needle in ("2,000 characters", "0.98", "0.95", "0.30", "1,000 draws", "seed 1", "8 USD", "1 USD", "flat 15%", "pressure-fail",
                   "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "0154ee5b1898a091c6efd00085541a7df0d2d07f2377cd20b191039ab6641174",
                   "5f5b9b7bde0450d2e6aae6ff9829ec8c859688f316c1fe561b03f4f0cddd49a2", "33 test days", "ex_top3", "NOT_DECIDABLE",
                   str(h.MIN_TEST_BOOSTS)):
        assert needle in doc, needle
    assert (h.MAX_PLAYBOOK_CHARS, h.PARSE_MIN, h.REFLECT_OK_MIN, h.MAX_BOOST_SHARE, h.BOOST_MULT) == (2000, 0.98, 0.95, 0.30, 2.0)
    assert (h.BOOT_DRAWS, h.BOOT_SEED, h.BURN_IN_DAYS, h.PLACEBO_DRAWS, h.N_LABEL_PCT) == (1000, 1, 3, 1000, 90.0)
