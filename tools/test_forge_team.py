"""Tests for tools/forge/team.py (Part B). Fake model and fake evaluator only: no API call, no tape read."""
from __future__ import annotations

import json
import os
import threading

import pytest

from tools import haiku_caller as hc
from tools.forge import specs as S
from tools.forge import team as T
from tools.forge.analyst_stats import analyst_stats

os.environ["FORGE_NO_SLEEP"] = "1"
FEATS = dict(T.FakeEvaluator.FEATURES)


def mk(tmp_path, ev=None, islands=2, budget=100.0, cost=0.001, **kw):
    ev = ev or T.FakeEvaluator()
    kw.setdefault("val_every_s", None)
    kw.setdefault("final_val", False)
    kw.setdefault("hours", 1.0)
    team = T.Team(tmp_path, ev, lambda i, led, b: T.FakeModel(b, FEATS, seed=i, cost=cost), FEATS, islands=islands,
                  budget_per_island=budget, log=lambda s: None, **kw)
    return team, ev


def rows(p):
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


GOOD = {"entry": [{"f": "f1", "op": ">", "v": 1}, {"f": "f2", "op": "<=", "v": 0.5}], "venue": 1, "cooldown_s": 0, "one_per_pool": True,
        "exit": {"tp": 0.5, "sl": 0.3, "trail": None, "max_hold_s": 600}, "stake_sol": 0.1}


# ---- parsing

def test_parse_fenced_and_trailing_comma():
    txt = "sure\n```json\n" + json.dumps({"specs": [{"spec": GOOD, "rationale": "r"}]})[:-2] + ",]}\n```\nbye"
    items, st = S.parse_specs(txt, FEATS)
    assert len(items) == 1 and st["parsed"] == 1 and st["json_failed"] == 0


def test_parse_repairs_and_rejects_are_counted():
    bad_feat = json.loads(json.dumps(GOOD))
    bad_feat["entry"][0]["f"] = "nope"
    wild = json.loads(json.dumps(GOOD))
    wild["exit"].update(tp=99, sl=5, max_hold_s=10 ** 9)
    wild["stake_sol"] = 5
    wild["entry"] = wild["entry"] * 4  # 8 conditions
    items, st = S.parse_specs(json.dumps({"specs": [{"spec": bad_feat}, {"spec": wild}, "junk", {"spec": {"entry": []}}]}), FEATS)
    assert st["parsed"] == 1 and st["repaired"] == 1 and st["rejected"] == 3
    sp = items[0]["spec"]
    assert sp["exit"]["tp"] == 10.0 and sp["exit"]["sl"] == 0.99 and sp["exit"]["max_hold_s"] == 86400
    assert len(sp["entry"]) == 6 and sp["stake_sol"] == 0.1


def test_parse_garbage_never_raises():
    for t in ("", "I cannot do that.", "{", "[1,2", None, '{"specs": 5}'):
        items, st = S.parse_specs(t, FEATS)
        assert items == [] and (st["json_failed"] == 1 or st["rejected"] >= 0)


def test_hash_is_order_independent():
    a = json.loads(json.dumps(GOOD))
    b = json.loads(json.dumps(GOOD))
    b["entry"].reverse()
    assert S.spec_hash(a) == S.spec_hash(b)
    b["entry"][0]["v"] = 2
    assert S.spec_hash(a) != S.spec_hash(b)


# ---- loop

def test_loop_runs_logs_and_dedupes(tmp_path):
    team, ev = mk(tmp_path, max_rounds=8)
    team.run()
    ev_rows = rows(tmp_path / "evals.jsonl")
    assert ev_rows and len({r["hash"] for r in ev_rows}) == len(ev_rows)  # no spec evaluated twice
    r0 = ev_rows[0]
    assert {"spec", "hash", "metrics", "fitness", "island", "round"} <= set(r0)
    assert ev.calls["TRAIN"] == len(ev_rows)
    assert team.counts["rejected"] > 0 and team.counts["json_failed"] > 0  # fake model emits garbage on purpose
    assert rows(tmp_path / "calls.jsonl") and (tmp_path / "state.json").exists()
    assert {r["island"] for r in ev_rows} == {0, 1}


def test_duplicate_spec_not_reevaluated(tmp_path):
    team, ev = mk(tmp_path)
    assert team.evaluate_train(GOOD, 0, 0, "t")[0] == "new"
    g2 = json.loads(json.dumps(GOOD))
    g2["entry"].reverse()
    assert team.evaluate_train(g2, 1, 0, "t")[0] == "dup"
    assert ev.calls["TRAIN"] == 1 and team.counts["dups"] == 1


def test_evaluator_error_is_contained(tmp_path):
    class Boom(T.FakeEvaluator):
        def evaluate(self, spec, split):
            raise RuntimeError("x")
    team, _ = mk(tmp_path, ev=Boom())
    assert team.evaluate_train(GOOD, 0, 0, "t")[0] == "error"
    assert team.counts["eval_errors"] == 1 and rows(tmp_path / "rejects.jsonl")


def test_migration_top2_to_next_island(tmp_path):
    team, _ = mk(tmp_path, islands=3, max_rounds=T.MIGRATE_EVERY)
    team.run()
    mig = rows(tmp_path / "migrations.jsonl")
    assert mig
    for m in mig:
        assert m["to"] == (m["from"] + 1) % 3
        assert {m["from"], m["to"]} <= team.archive[m["hash"]]["owners"]
    assert max(sum(1 for m in mig if m["from"] == i) for i in range(3)) <= T.MIGRANTS


def test_critic_updates_lessons_within_cap(tmp_path):
    team, _ = mk(tmp_path, islands=1, max_rounds=T.CRITIC_EVERY * 4)
    team.run()
    assert (tmp_path / "lessons.md").exists()
    txt = (tmp_path / "lessons.md").read_text()
    assert 0 < len(txt) <= T.LESSONS_CAP
    assert all(r["chars"] <= T.LESSONS_CAP for r in rows(tmp_path / "lessons_log.jsonl"))
    assert any(r["source"] == "critic" for r in rows(tmp_path / "evals.jsonl"))  # simplifications are evaluated too


def test_analyst_every_20_rounds(tmp_path):
    team, _ = mk(tmp_path, islands=1, max_rounds=21)
    team.run()
    an = [r for r in rows(tmp_path / "calls.jsonl") if r.get("role") == "analyst"]
    assert sorted(r["round"] for r in an) == [0, 20]
    assert "TRAIN statistics" in team.analyst_text or team.analyst_text.startswith("Forward")


# ---- VAL and shortlist

def test_val_read_once_per_spec_and_counted(tmp_path):
    team, ev = mk(tmp_path, max_rounds=6)
    team.run()
    n_before = ev.calls["VAL"]
    assert n_before == 0
    k1 = team.val_pass()
    assert 0 < k1 <= T.VAL_TOP
    val1 = [r for r in rows(tmp_path / "val.jsonl") if r["kind"] == "val"]
    assert len(val1) == k1 and len({r["hash"] for r in val1}) == k1
    seen = {r["hash"] for r in val1}
    team.val_pass()
    val2 = [r for r in rows(tmp_path / "val.jsonl") if r["kind"] == "val"]
    assert len(val2) == len({r["hash"] for r in val2})  # still no spec read twice
    assert len(team.val_read) == len(val2) and seen <= set(team.val_read)
    # only finite-fitness specs are ever VAL-read
    assert all(team.archive[h]["fitness"] > float("-inf") for h in team.val_read)


def test_shortlist_rule():
    ok = {"n": 15, "mean": 0.001, "ex_top3_total": 0.01, "positive_days": 3}
    assert T.Team.is_shortlist(ok)
    for k, v in (("n", 14), ("mean", 0.0), ("ex_top3_total", 0.0), ("positive_days", 2)):
        assert not T.Team.is_shortlist({**ok, k: v})


def test_shortlist_written_with_placebo(tmp_path):
    class Rigged(T.FakeEvaluator):
        def evaluate(self, spec, split):
            m = super().evaluate(spec, split)
            if split == "VAL":
                m.update(n=30, mean=0.01, ex_top3_total=0.1, positive_days=4)
            else:
                m.update(n=100, n_days=10, mean=0.01, ex_top3_total=0.5, ci90_low=0.004, best_day_share=0.2, trades_per_day=10)
            return m
    sl = tmp_path / "sub" / "SHORTLIST.jsonl"
    team, ev = mk(tmp_path, ev=Rigged(), max_rounds=2, shortlist_path=sl)
    team.run()
    team.val_pass()
    lines = rows(sl)
    assert lines and team.shortlist_n == len(lines)
    r = lines[0]
    assert r["train"]["n"] == 100 and r["val"]["n"] == 30
    assert r["placebo"]["draws"] == T.PLACEBO_DRAWS and "beats_placebo" in r["placebo"]
    assert team.val_placebo_reads >= T.PLACEBO_DRAWS
    assert len(team.val_read) == len({x["hash"] for x in rows(tmp_path / "val.jsonl") if x["kind"] == "val"})  # placebo does not enter it


def test_val_failure_not_shortlisted(tmp_path):
    class Weak(T.FakeEvaluator):
        def evaluate(self, spec, split):
            m = super().evaluate(spec, split)
            if split == "VAL":
                m.update(n=100, mean=-0.01, ex_top3_total=-1, positive_days=0)
            return m
    team, _ = mk(tmp_path, ev=Weak(), max_rounds=3)
    team.run()
    team.val_pass()
    assert team.shortlist_n == 0 and not (tmp_path / "SHORTLIST.jsonl").exists()


def test_placebo_permutes_entry_keeps_exits():
    ps = T.placebo_specs(GOOD, list(FEATS), "seed")
    assert len(ps) == T.PLACEBO_DRAWS
    for p in ps:
        assert p["exit"] == GOOD["exit"] and p["venue"] == GOOD["venue"]
        assert p["entry"] != GOOD["entry"]
        assert sorted(c["v"] for c in p["entry"]) == sorted(c["v"] for c in GOOD["entry"])
    assert T.placebo_specs(GOOD, list(FEATS), "seed") == ps  # deterministic


# ---- budget, resume, progress

def test_budget_hard_stop(tmp_path):
    team, _ = mk(tmp_path, islands=2, budget=0.05, cost=0.01, max_rounds=1000)
    team.run()
    assert [i.done for i in team.islands] == ["budget", "budget"]
    assert all(m.spent() <= 0.05 + 1e-9 for m in team.models)
    assert team.counts["calls"] <= 10


def test_resume_continues_and_does_not_reread(tmp_path):
    t1, _ = mk(tmp_path, max_rounds=3)
    t1.run()
    t1.val_pass()
    n_specs, nval = len(t1.archive), len(t1.val_read)
    st = json.loads((tmp_path / "state.json").read_text())
    assert [i["round"] for i in st["islands"]] == [3, 3]
    t2, ev2 = mk(tmp_path, max_rounds=5)
    assert len(t2.archive) == n_specs and len(t2.val_read) == nval and [i.round for i in t2.islands] == [3, 3]
    t2.run()
    assert [i.round for i in t2.islands] == [5, 5]
    t2.val_pass()
    v = [r["hash"] for r in rows(tmp_path / "val.jsonl") if r["kind"] == "val"]
    assert len(v) == len(set(v))


def test_resume_drops_cached_analyst_failure(tmp_path):
    t1, _ = mk(tmp_path, max_rounds=1)
    t1.run()
    st = json.loads((tmp_path / "state.json").read_text())
    st["analyst_text"] = "(TRAIN statistics failed: OSError)"
    (tmp_path / "state.json").write_text(json.dumps(st))
    t2, _ = mk(tmp_path, max_rounds=1)
    assert t2.analyst_text is None
    st["analyst_text"] = "real stats"
    (tmp_path / "state.json").write_text(json.dumps(st))
    t3, _ = mk(tmp_path, max_rounds=1)
    assert t3.analyst_text == "real stats"


def test_resume_keeps_spent_budget(tmp_path):
    t1, _ = mk(tmp_path, islands=1, budget=0.03, cost=0.01, max_rounds=100)
    t1.run()
    t2, _ = mk(tmp_path, islands=1, budget=0.03, cost=0.01, max_rounds=100)
    assert t2.islands[0].done == "budget"


def test_progress_file(tmp_path, monkeypatch):
    p = tmp_path / "prog.json"
    monkeypatch.setenv("MISCUSI_PROGRESS", str(p))
    team, _ = mk(tmp_path / "o", max_rounds=2)
    team.run()
    d = json.loads(p.read_text())
    assert d["pct"] == 100 and "spend $" in d["note"] and "shortlist=" in d["note"]


def test_timed_val_pass_runs_in_loop(tmp_path):
    team, ev = mk(tmp_path, max_rounds=40, val_every_s=0.01)
    team.run()
    assert ev.calls["VAL"] > 0


def test_cli_dry(tmp_path, capsys):
    rc = T.main(["--dry", "--out", str(tmp_path), "--islands", "2", "--budget-per-island", "1", "--hours", "0.01", "--max-rounds", "3"])
    out = capsys.readouterr().out
    assert rc == 0 and "= $2.00 total budget" in out and "FORGE done" in out


# ---- HaikuCaller integration (no network) and stats

def test_system_blocks_with_cache_control_are_accepted_by_haiku_caller():
    fixed = (T.PROMPTS / "fixed.md").read_text()
    assert "{{FEATURES}}" in fixed
    body = hc.build_body(hc.DEFAULT_MODEL, [{"type": "text", "text": fixed, "cache_control": {"type": "ephemeral"}},
                                            {"type": "text", "text": "ROLE"}], [{"role": "user", "content": "x"}], 100, None,
                         hc.DEFAULT_THINKING)
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_prompts_filled(tmp_path):
    team, _ = mk(tmp_path)
    assert "{{" not in team.fixed and "- f3: fake feature 3" in team.fixed
    for role in ("proposer", "analyst", "critic"):
        assert "{{" not in team.islands[0]._prompt(f"{role}.md")


def test_analyst_stats_shapes():
    import numpy as np
    rng = np.random.default_rng(0)
    feats = {f"x{i}": rng.normal(size=3000) for i in range(14)}
    fwd = {h: 0.05 * feats["x3"] + rng.normal(scale=0.1, size=3000) for h in ("1m", "5m", "30m", "2h", "24h")}
    text, st = analyst_stats(feats, fwd)
    assert st["ranked"][0] == "x3" and len(st["ranked"]) == 10 and len(st["inter"]) == 45 and len(text) <= 12000


def test_island_threads_share_state_safely(tmp_path):
    team, _ = mk(tmp_path, islands=8, max_rounds=5)
    team.run()
    assert threading.active_count() >= 1
    hs = [r["hash"] for r in rows(tmp_path / "evals.jsonl")]
    assert len(hs) == len(set(hs)) == team.counts["evaluated"]


def test_real_evaluator_interface_alignment():
    import numpy as np
    from tools.forge import evaluate as E, common as C, team as T
    assert set(T.resolve_features(type("X", (), {"FEATURES": E.FEATURES})())) == set(C.FEATURES)
    m = E.metrics_of(np.array([0.01] * 50 + [-0.005] * 10), np.arange(60) * 86_400_000 // 6, np.zeros(60, np.int64), np.ones(60, np.int64),
                     np.zeros(60, int), 15.0)
    assert T.mget(m, "n") == 60 and T.mget(m, "pos_days") == m["positive_days"] and T.mget(m, "ex_top3") == m["ex_top3_total"]
    assert T.mget(m, "ci") == m["ci90_low"] and T.mget(m, "tpd") == m["trades_per_day"]
    assert T.Team.is_shortlist({"n": 20, "mean": 0.01, "ex_top3_total": 0.1, "positive_days": 3, "fitness": float("-inf")})
