"""Tests for the successor-book part of tools/a3_halt.py: the optional fees_check_bot_ids books field and the fees_check sub-verdict. No network. test_a3_halt.py is unchanged;
its fixtures are imported by name."""
from __future__ import annotations

import copy
import json

import pytest

from tools import a3_halt as h
from tools import arena_h5_score as sc
from tools.test_a3_halt import BOOKS, DEPLOY_MS, NOW, RUN_MS, ends, make_registry, verdict

SUCC = "h5v1s-boostfloor-a3"  # a successor book (registered after the fees-program rule)
FEES_MS = DEPLOY_MS + 3600_000


def registry_with_successor(tmp_path):
    path = make_registry(tmp_path)
    bots = sc.load_registry(path)
    bots[SUCC] = copy.deepcopy(bots["h5v1-boostfloor-a2"])
    sc._atomic_write_json(path, {"bots": bots})
    return path


def books(fees=(SUCC,)):
    b = {"legacy_bot_ids": list(BOOKS["legacy_bot_ids"]), "new_rule_bot_ids": BOOKS["new_rule_bot_ids"] + [SUCC]}
    if fees is not None:
        b[h.FEES_KEY] = list(fees)
    return b


def fees_verdict(sub, halt=None, main_halt=RUN_MS):
    return verdict("HALT", flag=RUN_MS, halt=main_halt, fees_check={"schema": "a3_fees_check.v1", "verdict": sub, "halt_at_ms": halt, "reasons": ["r"], "redeploys": []})


def test_without_the_field_a_sub_verdict_is_ignored_and_everything_halts(tmp_path):
    p1, p2 = registry_with_successor(tmp_path / "a" if (tmp_path / "a").mkdir() is None else None), registry_with_successor(tmp_path / "b" if (tmp_path / "b").mkdir() is None else None)
    r1 = h.apply(verdict("HALT", flag=RUN_MS, halt=RUN_MS), books(fees=None), p1, NOW)
    r2 = h.apply(fees_verdict("PASS"), books(fees=None), p2, NOW)
    assert r1 == r2 and "fees_check" not in r2 and ends(p1) == ends(p2) and ends(p2)[SUCC] == RUN_MS


def test_empty_field_is_the_merged_rule(tmp_path):
    p = registry_with_successor(tmp_path)
    out = h.apply(fees_verdict("PASS"), books(fees=()), p, NOW)
    assert ends(p)[SUCC] == RUN_MS and "fees_check" not in out


def test_pass_keeps_only_the_listed_successor_open(tmp_path):
    p = registry_with_successor(tmp_path)
    out = h.apply(fees_verdict("PASS"), books(), p, NOW)
    e = ends(p)
    assert e[SUCC] is None and out["fees_check"] == "PASS"
    assert e["h5v1-boostfloor-a2"] == e["h5v2-boostclock-a2"] == e["h5v1-q50-a2"] == RUN_MS and e["h5v2-boostclock-a1"] == RUN_MS
    assert [x["rule"] for x in out["applied"]] == ["legacy", "new_rule"]


@pytest.mark.parametrize("sub", ["FAIL", "ERROR"])
def test_fail_or_error_ends_the_successor_at_the_fees_write_instant(sub, tmp_path):
    p = registry_with_successor(tmp_path)
    out = h.apply(fees_verdict(sub, halt=FEES_MS), books(), p, NOW)
    e = ends(p)
    assert e[SUCC] == FEES_MS and e["h5v1-q50-a2"] == RUN_MS
    assert [x["rule"] for x in out["applied"]] == ["legacy", "new_rule", "fees_check"]


def test_main_halt_without_a_sub_verdict_ends_the_successor_too(tmp_path):
    p = registry_with_successor(tmp_path)
    h.apply(verdict("HALT", flag=RUN_MS, halt=DEPLOY_MS), books(), p, NOW)
    assert ends(p)[SUCC] == DEPLOY_MS


def test_successor_alone_in_new_rule_with_pass_is_not_an_empty_list_error(tmp_path):
    p = registry_with_successor(tmp_path)
    b = {"legacy_bot_ids": BOOKS["legacy_bot_ids"] + BOOKS["new_rule_bot_ids"], "new_rule_bot_ids": [SUCC], h.FEES_KEY: [SUCC]}
    out = h.apply(fees_verdict("PASS"), b, p, NOW)
    assert ends(p)[SUCC] is None and [x["rule"] for x in out["applied"]] == ["legacy"]


BAD_BOOKS = {
    "not_a_list": {h.FEES_KEY: SUCC},
    "duplicate": {h.FEES_KEY: [SUCC, SUCC]},
    "not_new_rule": {h.FEES_KEY: ["h5v2-boostclock-a1"]},
    "q50_refused": None,
}


@pytest.mark.parametrize("name", list(BAD_BOOKS))
def test_bad_fees_field_is_refused_and_writes_nothing(name, tmp_path):
    p = registry_with_successor(tmp_path)
    if name == "q50_refused":
        bots = sc.load_registry(p)
        bots["h5v1q50-boostfloor-a1"] = copy.deepcopy(bots[SUCC])
        sc._atomic_write_json(p, {"bots": bots})
        b = books(fees=(SUCC, "h5v1q50-boostfloor-a1"))
        b["new_rule_bot_ids"].append("h5v1q50-boostfloor-a1")
    else:
        b = {**books(), **BAD_BOOKS[name]}
    before = p.read_text()
    with pytest.raises(h.Invalid):
        h.apply(fees_verdict("PASS"), b, p, NOW)
    assert p.read_text() == before


@pytest.mark.parametrize("name,v", [
    ("pass_with_instant", fees_verdict("PASS", halt=FEES_MS)),
    ("fail_without_instant", fees_verdict("FAIL")),
    ("after_flag", fees_verdict("FAIL", halt=RUN_MS + 1)),
    ("unknown", fees_verdict("MAYBE")),
    ("not_object", verdict("HALT", flag=RUN_MS, halt=RUN_MS, fees_check="PASS")),
    ("beside_a_pass", verdict("PASS", flag=RUN_MS, fees_check={"verdict": "PASS", "halt_at_ms": None})),
    ("bool_instant", fees_verdict("FAIL", halt=True)),
])
def test_malformed_sub_verdict_is_refused_and_writes_nothing(name, v, tmp_path):
    p = registry_with_successor(tmp_path)
    before = p.read_text()
    with pytest.raises(h.Invalid):
        h.apply(v, books(), p, NOW)
    assert p.read_text() == before


def test_plan_without_fees_arguments_is_the_merged_plan():
    d = h.decision_from_verdict(verdict("HALT", flag=RUN_MS, halt=DEPLOY_MS))
    merged = h.plan(d, BOOKS["legacy_bot_ids"], BOOKS["new_rule_bot_ids"])
    assert h.plan(d, BOOKS["legacy_bot_ids"], BOOKS["new_rule_bot_ids"], [SUCC], None) == merged
    assert h.plan(d, BOOKS["legacy_bot_ids"], BOOKS["new_rule_bot_ids"], (), {"verdict": "PASS", "halt_at_ms": None}) == merged


def test_deadman_still_covers_the_successor(tmp_path):
    p = registry_with_successor(tmp_path)
    out = h.deadman(tmp_path / "none.jsonl", books(), p, now_ms=NOW + 400 * 3600_000)
    assert out["deadman"] == "FIRED" and ends(p)[SUCC] is not None


def test_cli_apply_with_the_field(tmp_path):
    import time
    p = registry_with_successor(tmp_path)
    now = int(time.time() * 1000)
    run_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    run_ms = h.utc_ms(run_utc)
    v = verdict("HALT", flag=run_ms, halt=run_ms, run_utc=run_utc, fees_check={"verdict": "PASS", "halt_at_ms": None})
    (tmp_path / "v.json").write_text(json.dumps(v))
    (tmp_path / "b.json").write_text(json.dumps(books()))
    rc = h.main(["apply", "--verdict", str(tmp_path / "v.json"), "--applied", str(tmp_path / "ap.jsonl"), "--books", str(tmp_path / "b.json"), "--arena-root", str(p.parent)])
    assert rc == 0 and ends(p)[SUCC] is None and ends(p)["h5v1-q50-a2"] == run_ms and now > 0
    line = json.loads((tmp_path / "ap.jsonl").read_text().splitlines()[-1])
    assert line["verdict"] == "HALT" and SUCC not in json.dumps(line["ended"])
