"""Tests for tools/synthetic_class.py (EXP-024 Amendment 4, B1 and B4). Fixture-based: no network.

Recorded fixtures: tools/fixtures/synthetic_class/recorded.json, public mainnet RPC answers from the 2026-10-09 structure-only
measurement, three post-redeploy graduations whose completing tx carries a PostCompleteBuyEvent and three whose completing tx does not
(transactions trimmed to what `tx_event_blobs` reads; no H5 fill, size, exit or P&L). Constructed inputs are labelled `spliced`: no
recorded graduation has the event in the migrate tx, or the CompleteEvent in the migrate tx, so those are built by moving a recorded
event log line from one recorded tx into another.
"""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import pytest

from tools import pump_structure_monitor as M
from tools import synthetic_class as sc

FX = json.loads((Path(__file__).parent / "fixtures" / "synthetic_class" / "recorded.json").read_text())
CASES = {c["name"]: c for c in FX["cases"]}
SYN = [n for n in CASES if n.startswith("synthetic")]
NON = [n for n in CASES if n.startswith("non_synthetic")]


def _entry(sig: str, err=None) -> dict:
    return {"signature": sig, "slot": 1, "err": err, "blockTime": 1}


class FakeRpc:
    """Serves getTransaction from a dict and getSignaturesForAddress from newest-first lists; counts what it served."""

    def __init__(self, txs: dict, lists: dict, *, raise_on: dict | None = None):
        self.txs = copy.deepcopy(txs)
        self.lists = {k: list(v) for k, v in lists.items()}
        self.raise_on = raise_on or {}
        self.tx_calls: list[str] = []
        self.sig_calls: list[tuple[str, dict]] = []
        self.sig_entries_served = 0

    def call(self, method, params):
        if method == "getTransaction":
            sig = params[0]
            assert params[1]["maxSupportedTransactionVersion"] == 1 and params[1]["commitment"] == "finalized"
            self.tx_calls.append(sig)
            if sig in self.raise_on:
                raise self.raise_on[sig]
            return copy.deepcopy(self.txs.get(sig))
        assert method == "getSignaturesForAddress", method
        addr, opts = params
        self.sig_calls.append((addr, dict(opts)))
        assert opts["commitment"] == "finalized" and 1 <= opts["limit"] <= 1000
        lst = self.lists.get(addr, [])
        start = 0
        if opts.get("before"):
            idx = next((i for i, s in enumerate(lst) if s["signature"] == opts["before"]), None)
            if idx is None:
                raise M.RpcError("before signature not found")
            start = idx + 1
        page = lst[start : start + opts["limit"]]
        self.sig_entries_served += len(page)
        return page


def fake_for(name: str, **kw) -> tuple[FakeRpc, dict]:
    c = CASES[name]
    lists = {
        c["pool"]: [_entry(c["boundary_sig"])] + c["pool_sigs"],  # the recorded list is the one served for before=boundary_sig
        c["curve"]: [_entry(c["migrate_sig"])] + c["curve_sigs"],
    }
    return FakeRpc(FX["txs"], lists, **kw), c


def args(c: dict, **extra) -> dict:
    return {"mint": c["mint"], "pool": c["pool"], **extra}


def _line_with_disc(tx: dict, disc: bytes) -> str:
    for ln in tx["meta"]["logMessages"]:
        if "Program data: " in ln and base64.b64decode(ln.split("Program data: ", 1)[1])[:8] == disc:
            return ln
    raise AssertionError("no such event line in the recorded tx")


# ---- the PDA --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(CASES))
def test_curve_pda_derivation_matches_the_recorded_curve(name):
    assert sc.curve_pda_for_mint(CASES[name]["mint"]) == CASES[name]["curve"]


# ---- recorded graduations -------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(CASES))
def test_recorded_graduation_is_classified_by_the_b4_search(name):
    rpc, c = fake_for(name)
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert tuple(out) == sc.RESULT_KEYS
    assert out["class"] == c["expect"]
    assert out["event_seen_any"] is (c["expect"] == "synthetic")
    assert out["migrate_sig"] == c["migrate_sig"] and out["complete_sig"] == c["complete_sig"]
    assert out["reason"] == ("post_complete_buy_in_complete_tx" if c["expect"] == "synthetic" else "no_post_complete_buy")
    assert [a for a, _ in rpc.sig_calls] == [c["pool"], c["curve"]]
    assert rpc.sig_calls[0][1]["before"] == c["boundary_sig"] and rpc.sig_calls[1][1]["before"] == c["migrate_sig"]


def test_the_recorded_set_has_both_classes():
    assert len(SYN) == 3 and len(NON) == 3
    for n in SYN:  # the recorded synthetic completing txs carry the event; the migrate txs do not
        assert sc.M.post_complete_buy_seen(M.tx_event_blobs(FX["txs"][CASES[n]["complete_sig"]]), CASES[n]["mint"])[0]
        assert not sc.M.post_complete_buy_seen(M.tx_event_blobs(FX["txs"][CASES[n]["migrate_sig"]]), CASES[n]["mint"])[0]


def test_s0_print_is_tried_first_and_a_migrate_tx_s0_needs_no_pool_search():
    rpc, c = fake_for("synthetic_1")
    out = sc.classify_pool(rpc, **args(c, s0_sig=c["migrate_sig"]))
    assert out["class"] == "synthetic" and out["migrate_sig"] == c["migrate_sig"]
    assert [a for a, _ in rpc.sig_calls] == [c["curve"]]  # the pool's signature list was never read


def test_s0_that_is_not_the_migrate_tx_falls_through_to_the_pool_search_before_s0():
    rpc, c = fake_for("non_synthetic_1")
    out = sc.classify_pool(rpc, **args(c, s0_sig=c["boundary_sig"]))
    assert out["class"] == "non_synthetic" and out["migrate_sig"] == c["migrate_sig"]
    assert rpc.sig_calls[0][1]["before"] == c["boundary_sig"]  # `before` is the s0 print's signature
    assert rpc.tx_calls[0] == c["boundary_sig"]  # ... after the s0 tx itself was tested (and missing from the fake)


def test_tape_signatures_only_locate_and_need_no_signature_search():
    rpc, c = fake_for("synthetic_2")
    out = sc.classify_pool(rpc, **args(c, tape_migrate_sig=c["migrate_sig"], tape_complete_sig=c["complete_sig"]))
    assert out["class"] == "synthetic" and rpc.sig_calls == []
    assert sorted(rpc.tx_calls) == sorted([c["migrate_sig"], c["complete_sig"]])


def test_the_tape_never_settles_a_class_without_the_transactions():
    rpc, c = fake_for("non_synthetic_1", raise_on={})
    rpc.txs.pop(c["complete_sig"])  # the tape names it, the node does not have it
    out = sc.classify_pool(rpc, **args(c, tape_migrate_sig=c["migrate_sig"], tape_complete_sig=c["complete_sig"]))
    assert out["class"] == "unclassified" and out["migrate_sig"] == c["migrate_sig"] and out["complete_sig"] is None


# ---- the event in the migrate tx only (spliced) -----------------------------------------------------------------


def test_post_complete_buy_in_the_migrate_tx_only_is_synthetic_spliced():
    rpc, c = fake_for("non_synthetic_1")
    donor = FX["txs"][CASES["synthetic_1"]["complete_sig"]]
    line = _line_with_disc(donor, M.DISC_POST_COMPLETE_BUY)
    rpc.txs[c["migrate_sig"]]["meta"]["logMessages"].append(line)
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["class"] == "synthetic" and out["reason"] == "post_complete_buy_in_migrate_tx"
    assert out["complete_sig"] == c["complete_sig"] != out["migrate_sig"]


def test_one_tx_that_is_both_migrate_and_completing_is_tested_once_spliced():
    rpc, c = fake_for("non_synthetic_2")
    own_complete = FX["txs"][c["complete_sig"]]  # this mint's own CompleteEvent line, moved into its migrate tx
    donor = FX["txs"][CASES["synthetic_1"]["complete_sig"]]
    rpc.txs[c["migrate_sig"]]["meta"]["logMessages"] += [_line_with_disc(own_complete, M.DISC_COMPLETE), _line_with_disc(donor, M.DISC_POST_COMPLETE_BUY)]
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["class"] == "synthetic" and out["migrate_sig"] == out["complete_sig"] == c["migrate_sig"]
    assert out["reason"] == "post_complete_buy_in_migrate_and_complete_tx"
    assert [a for a, _ in rpc.sig_calls] == [c["pool"]]  # no curve search: the migrate tx carries the CompleteEvent


def test_post_complete_buy_in_one_tx_with_the_other_unreadable_stays_unclassified_but_event_seen_any_is_true():
    rpc, c = fake_for("synthetic_1")
    rpc.txs[c["migrate_sig"]]["meta"]["logMessages"].append(_line_with_disc(FX["txs"][c["complete_sig"]], M.DISC_POST_COMPLETE_BUY))
    rpc.txs.pop(c["complete_sig"])
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["class"] == "unclassified" and out["reason"] == "tx_missing:complete"  # B1/B3 as written: the class stays literal
    assert out["event_seen_any"] is True  # ... and the audit's separate field records that the event WAS seen in a readable located tx


# ---- defining-event check and fallback to the next source ------------------------------------------------------------


def test_tape_migrate_sig_without_the_migration_event_falls_back_to_the_pool_search():
    rpc, c = fake_for("synthetic_1")
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"], tape_migrate_sig=c["complete_sig"]))  # a real tx, not a migrate tx
    assert out["class"] == "synthetic" and out["migrate_sig"] == c["migrate_sig"]
    assert rpc.tx_calls[0] == c["complete_sig"] and len(rpc.sig_calls) == 2


def test_tape_complete_sig_without_this_mints_complete_event_falls_back_to_the_curve_search():
    rpc, c = fake_for("synthetic_2")
    other = CASES["non_synthetic_1"]  # a real CompleteEvent tx, for a different mint
    rpc.txs[other["complete_sig"]] = FX["txs"][other["complete_sig"]]
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"], tape_complete_sig=other["complete_sig"]))
    assert out["class"] == "synthetic" and out["complete_sig"] == c["complete_sig"]
    assert other["complete_sig"] in rpc.tx_calls and rpc.sig_calls[-1][0] == c["curve"]


def test_a_tape_tx_that_failed_on_chain_is_not_used_even_with_the_right_event():
    rpc, c = fake_for("synthetic_3")
    rpc.txs[c["complete_sig"]]["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    rpc.lists[c["curve"]] = [_entry(c["migrate_sig"]), _entry(c["complete_sig"])]  # the node lists it as ok, the tx itself says it failed
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"], tape_complete_sig=c["complete_sig"]))
    assert out["class"] == "unclassified" and out["reason"] == "complete_tx_not_found"  # skipped as the tape source and again in the curve search


def test_curve_search_skips_failed_signatures_without_fetching_them_and_takes_the_newest_success():
    rpc, c = fake_for("synthetic_1")
    failed = [_entry(f"failed{i}", err={"InstructionError": [0, "x"]}) for i in range(30)]
    rpc.lists[c["curve"]] = [_entry(c["migrate_sig"])] + failed + c["curve_sigs"]
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["class"] == "synthetic" and out["complete_sig"] == c["complete_sig"]
    assert not [s for s in rpc.tx_calls if s.startswith("failed")]


def test_curve_search_takes_the_newest_complete_event_tx_not_the_oldest():
    rpc, c = fake_for("non_synthetic_1")
    newer = "newer_complete_tx"
    rpc.txs[newer] = copy.deepcopy(FX["txs"][c["complete_sig"]])  # same CompleteEvent, newer position, and it carries the PostCompleteBuy
    rpc.txs[newer]["meta"]["logMessages"].append(_line_with_disc(FX["txs"][CASES["synthetic_1"]["complete_sig"]], M.DISC_POST_COMPLETE_BUY))
    rpc.lists[c["curve"]] = [_entry(c["migrate_sig"]), _entry(newer)] + c["curve_sigs"]
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["complete_sig"] == newer and out["class"] == "synthetic"


def test_pool_search_takes_the_oldest_success_and_does_not_fetch_the_newer_ones():
    rpc, c = fake_for("non_synthetic_3")
    older_failed = [_entry("old_failed", err={"InstructionError": [0, "x"]})]
    rpc.lists[c["pool"]] = rpc.lists[c["pool"]] + older_failed  # a failed sniper tx older than the migrate tx
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["migrate_sig"] == c["migrate_sig"]
    pool_fetches = [s for s in rpc.tx_calls if s in {e["signature"] for e in c["pool_sigs"]}]
    assert pool_fetches == [c["migrate_sig"]]  # oldest successful first; nothing newer was fetched


# ---- the cap ----------------------------------------------------------------------------------------------------------


def test_curve_search_cap_is_on_signatures_across_pages():
    rpc, c = fake_for("synthetic_1")
    junk = [_entry(f"junk{i}", err={"InstructionError": [0, "x"]}) for i in range(2500)]
    rpc.lists[c["curve"]] = [_entry(c["migrate_sig"])] + junk + c["curve_sigs"]  # the completing tx is 2500 signatures back
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]), cap=1000, page_size=300)
    assert out["class"] == "unclassified" and out["reason"] == "complete_tx_not_found" and out["complete_sig"] is None
    curve_calls = [o for a, o in rpc.sig_calls if a == c["curve"]]
    assert [o["limit"] for o in curve_calls] == [300, 300, 300, 100]  # the last page is trimmed so the total is exactly the cap
    assert sum(1 for s in rpc.tx_calls if s.startswith("junk")) == 0
    assert curve_calls[1]["before"] == "junk299"  # paginated newest-first by `before`


def test_curve_search_finds_the_tx_when_it_is_inside_the_cap():
    rpc, c = fake_for("synthetic_1")
    junk = [_entry(f"junk{i}", err={"InstructionError": [0, "x"]}) for i in range(900)]
    rpc.lists[c["curve"]] = [_entry(c["migrate_sig"])] + junk + c["curve_sigs"]
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]), cap=1000, page_size=300)
    assert out["class"] == "synthetic" and out["complete_sig"] == c["complete_sig"]


def test_pool_search_cap_leaves_the_pool_unclassified_when_the_migrate_tx_is_older_than_the_cap():
    rpc, c = fake_for("non_synthetic_1")
    newer = [_entry(f"trade{i}") for i in range(1200)]
    rpc.lists[c["pool"]] = [_entry(c["boundary_sig"])] + newer + c["pool_sigs"]
    rpc.txs.update({e["signature"]: {"slot": 1, "meta": {"err": None, "logMessages": []}, "transaction": {"message": {"accountKeys": []}}} for e in newer})
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]), cap=1000)
    assert out["class"] == "unclassified" and out["reason"] == "migrate_tx_not_found" and out["migrate_sig"] is None
    assert rpc.sig_entries_served == 1000  # not one signature beyond the cap was requested
    assert c["migrate_sig"] not in rpc.tx_calls


# ---- failures ------------------------------------------------------------------------------------------------------


def test_a_fetch_that_fails_after_retries_is_unclassified():
    rpc, c = fake_for("synthetic_1", raise_on={})
    rpc.raise_on[CASES["synthetic_1"]["complete_sig"]] = M.RpcUnreachable("getTransaction: http 429 after 6 attempts")
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["class"] == "unclassified" and out["reason"].startswith("fetch_failed:complete") and out["migrate_sig"] == c["migrate_sig"]


def test_a_signature_list_that_fails_is_unclassified_and_the_call_cap_is_a_failure_too():
    rpc, c = fake_for("synthetic_1")
    rpc.lists.pop(c["pool"])
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))  # unknown `before` -> the fake raises RpcError
    assert out["class"] == "unclassified" and out["reason"].startswith("fetch_failed:signatures:pool")
    rpc2, c = fake_for("synthetic_1", raise_on={c["migrate_sig"]: M.CallBudgetExceeded("call cap")})
    out2 = sc.classify_pool(rpc2, **args(c, before_sig=c["boundary_sig"]))
    assert out2["class"] == "unclassified" and out2["reason"].startswith("fetch_failed:migrate")


def test_a_missing_migrate_tx_is_unclassified():
    rpc, c = fake_for("non_synthetic_1")
    rpc.txs.pop(c["migrate_sig"])
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["class"] == "unclassified" and out["reason"] == "tx_missing:migrate"


def test_no_anchor_and_no_tape_searches_from_the_newest_signature():
    rpc, c = fake_for("non_synthetic_1")
    rpc.lists[c["pool"]] = c["pool_sigs"]  # a young pool: the whole history is inside the cap
    out = sc.classify_pool(rpc, **args(c))
    assert out["class"] == "non_synthetic" and "before" not in rpc.sig_calls[0][1]


def test_curve_pda_may_be_given_and_is_used_as_is():
    rpc, c = fake_for("synthetic_1")
    out = sc.classify_pool(rpc, **args(c, curve_pda=c["curve"], before_sig=c["boundary_sig"]))
    assert out["class"] == "synthetic" and rpc.sig_calls[1][0] == c["curve"]


def test_recorded_pool_with_an_older_successful_non_createpool_tx_is_walked_past():
    """synthetic_1 is real: a successful tx on the pool address, 1,367 slots before the migrate tx, carries no CreatePoolEvent.
    The oldest-first walk fetches it, rejects it, and takes the migrate tx as the oldest successful tx that carries one."""
    rpc, c = fake_for("synthetic_1")
    older = [e["signature"] for e in c["pool_sigs"][c["pool_sigs"].index(next(e for e in c["pool_sigs"] if e["signature"] == c["migrate_sig"])) + 1 :]]
    assert len(older) == 1 and older[0] in FX["txs"]
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["migrate_sig"] == c["migrate_sig"] and out["class"] == "synthetic"
    assert rpc.tx_calls[:2] == [older[0], c["migrate_sig"]]


def test_event_seen_any_is_false_when_nothing_readable_shows_the_event():
    rpc, c = fake_for("non_synthetic_1")
    rpc.txs.pop(c["complete_sig"])
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]))
    assert out["class"] == "unclassified" and out["event_seen_any"] is False
    assert tuple(out) == sc.RESULT_KEYS


def test_event_seen_any_from_a_tape_located_completing_tx_when_the_migrate_tx_cannot_be_found():
    rpc, c = fake_for("synthetic_1")
    rpc.txs.pop(c["migrate_sig"])
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"], tape_complete_sig=c["complete_sig"]))
    assert out["class"] == "unclassified" and out["reason"] == "tx_missing:migrate" and out["event_seen_any"] is True


# ---- audit-only migrate fallback (not B4) -----------------------------------------------------------------------------

UNKNOWN_BEFORE = "U" * 40 + "NKNOWNSIG"  # what a buy that never landed leaves behind: a signature the node does not have
STUB = {"slot": 1, "meta": {"err": None, "logMessages": []}, "transaction": {"message": {"accountKeys": []}}}


def test_audit_fallback_defaults_off_and_the_b4_read_path_is_unchanged():
    import inspect

    assert inspect.signature(sc.classify_pool).parameters["audit_fallback"].default is False
    rpc, c = fake_for("synthetic_1")
    out = sc.classify_pool(rpc, **args(c, before_sig=UNKNOWN_BEFORE))
    assert out["class"] == "unclassified" and out["reason"] == "fetch_failed:signatures:pool:RpcError"  # B4: a pool search that fails is unclassified
    assert [a for a, _ in rpc.sig_calls] == [c["pool"]]  # the curve was never searched without `before`
    ok, c = fake_for("synthetic_1")
    out = sc.classify_pool(ok, **args(c, before_sig=c["boundary_sig"]))
    assert out["reason"] == "post_complete_buy_in_complete_tx" and "via_audit_fallback" not in out["reason"]
    assert all("before" in o for _, o in ok.sig_calls)


@pytest.mark.parametrize("name", list(CASES))
def test_audit_fallback_classifies_a_never_landed_anchor_like_the_b4_path(name):
    ref_rpc, c = fake_for(name)
    ref = sc.classify_pool(ref_rpc, **args(c, before_sig=c["boundary_sig"]))
    rpc, c = fake_for(name)
    out = sc.classify_pool(rpc, **args(c, before_sig=UNKNOWN_BEFORE), audit_fallback=True)
    assert out["class"] == c["expect"] == ref["class"]
    assert (out["migrate_sig"], out["complete_sig"], out["event_seen_any"]) == (ref["migrate_sig"], ref["complete_sig"], ref["event_seen_any"])
    assert out["reason"] == ref["reason"] + ";via_audit_fallback"
    curve_calls = [o for a, o in rpc.sig_calls if a == c["curve"]]
    assert "before" not in curve_calls[0] and curve_calls[0]["limit"] == 1000  # the fallback: newest-first, no `before`
    assert curve_calls[1]["before"] == c["migrate_sig"]  # then B4 resumes: the completing-tx search before the migrate sig


def test_audit_fallback_takes_the_newest_tx_with_this_pools_createpool_and_this_mints_migration_event():
    rpc, c = fake_for("non_synthetic_1")
    other = CASES["non_synthetic_2"]  # a real migrate tx, for a different pool and mint
    rpc.txs[other["migrate_sig"]] = FX["txs"][other["migrate_sig"]]
    rpc.txs["stub_ok"] = copy.deepcopy(STUB)
    failed = [_entry(f"failed{i}", err={"InstructionError": [0, "x"]}) for i in range(5)]
    rpc.lists[c["curve"]] = failed + [_entry("stub_ok"), _entry(other["migrate_sig"]), _entry(c["migrate_sig"])] + c["curve_sigs"]
    out = sc.classify_pool(rpc, **args(c, before_sig=UNKNOWN_BEFORE), audit_fallback=True)
    assert out["class"] == "non_synthetic" and out["migrate_sig"] == c["migrate_sig"]
    assert [s for s in rpc.tx_calls if s in ("stub_ok", other["migrate_sig"], c["migrate_sig"])][:3] == ["stub_ok", other["migrate_sig"], c["migrate_sig"]]
    assert not [s for s in rpc.tx_calls if s.startswith("failed")]


def test_audit_fallback_also_runs_when_the_pool_search_ends_at_the_cap_and_a_failed_fallback_keeps_both_reasons():
    rpc, c = fake_for("non_synthetic_1")
    newer = [_entry(f"trade{i}") for i in range(1200)]
    rpc.lists[c["pool"]] = [_entry(c["boundary_sig"])] + newer + c["pool_sigs"]
    rpc.txs.update({e["signature"]: copy.deepcopy(STUB) for e in newer})
    out = sc.classify_pool(rpc, **args(c, before_sig=c["boundary_sig"]), cap=1000, audit_fallback=True)
    assert out["class"] == "non_synthetic" and out["migrate_sig"] == c["migrate_sig"] and out["reason"].endswith(";via_audit_fallback")
    rpc2, c = fake_for("non_synthetic_1")
    rpc2.txs["stub_ok"] = copy.deepcopy(STUB)
    rpc2.lists[c["curve"]] = [_entry("stub_ok")]  # the curve holds no migrate tx either
    out = sc.classify_pool(rpc2, **args(c, before_sig=UNKNOWN_BEFORE), audit_fallback=True)
    assert out["class"] == "unclassified" and out["migrate_sig"] is None
    assert out["reason"] == "fetch_failed:signatures:pool:RpcError;fallback:migrate_tx_not_found"


def test_audit_fallback_respects_the_cap_and_a_missing_tx_in_its_walk_is_unclassified():
    rpc, c = fake_for("synthetic_1")
    junk = [_entry(f"junk{i}", err={"InstructionError": [0, "x"]}) for i in range(1500)]
    rpc.lists[c["curve"]] = junk + [_entry(c["migrate_sig"])] + c["curve_sigs"]  # the migrate tx is 1500 signatures back
    out = sc.classify_pool(rpc, **args(c, before_sig=UNKNOWN_BEFORE), cap=1000, page_size=400, audit_fallback=True)
    assert out["class"] == "unclassified" and out["reason"].endswith("fallback:migrate_tx_not_found")
    assert [o["limit"] for a, o in rpc.sig_calls if a == c["curve"]] == [400, 400, 200]
    rpc2, c = fake_for("synthetic_1")
    rpc2.lists[c["curve"]] = [_entry("not_in_the_node"), _entry(c["migrate_sig"])]
    out = sc.classify_pool(rpc2, **args(c, before_sig=UNKNOWN_BEFORE), audit_fallback=True)
    assert out["class"] == "unclassified" and out["reason"].endswith("fallback:tx_missing:migrate_fallback")


def test_before_fallback_hook_runs_once_just_before_the_fallback_and_never_on_the_b4_path():
    events = []

    def watch(rpc, c):
        orig = rpc.call

        def rec(method, params):
            nofall = method == "getSignaturesForAddress" and params[0] == c["curve"] and "before" not in params[1]
            events.append("curve_no_before" if nofall else method)
            return orig(method, params)

        rpc.call = rec

    rpc, c = fake_for("synthetic_1")
    watch(rpc, c)
    out = sc.classify_pool(rpc, **args(c, before_sig=UNKNOWN_BEFORE), audit_fallback=True, before_fallback=lambda: events.append("HOOK"))
    assert out["class"] == "synthetic" and events[:3] == ["getSignaturesForAddress", "HOOK", "curve_no_before"] and events.count("HOOK") == 1
    for kw in ({"before_sig": None}, {"before_sig": UNKNOWN_BEFORE}):  # B4 path with the pool search fine, or the flag off
        events.clear()
        rpc, c = fake_for("synthetic_1")
        watch(rpc, c)
        kw = dict(kw, before_sig=c["boundary_sig"]) if kw["before_sig"] is None else kw
        sc.classify_pool(rpc, **args(c, **kw), audit_fallback=kw["before_sig"] != UNKNOWN_BEFORE, before_fallback=lambda: events.append("HOOK"))
        assert "HOOK" not in events
