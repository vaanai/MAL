"""Tests for tools/arena_close_blocked.py. Fixtures only: a mocked RPC, a decision log written with DecisionLog. No network, no env file."""

from __future__ import annotations

import json

import pytest

from tools import arena_close_blocked as C
from tools import arena_report_legs as RL
from tools import mint_account_fetch as MF
from tools.arena_declog import DecisionLog

SPL, T22 = MF.SPL_TOKEN_PROGRAM, MF.TOKEN_2022_PROGRAM
GOOD = "9VEpuoCRJxHddJmgzuHHCSbGoXaubRSaVu8jNxKcpump"
FREEZE = "A9DtkpXBcz2o2K3iG7xniDbWktvHRtEbq7XNyNBvpump"
EXT = "3wCMJ9CTtDnnvaUq8WfQ3e3YbH3eVgJnB5UW8s1Tpump"
FAIL = "4cZ5eVEXw6M3d8q9Zk1u2h7FmWqXnR5sYtJ8bA3Lpump"
SPLM = "5kP2dQe7vNfXr1aH9uTz3GcYwLmB6sJ4oVx8nD2Rpump"
BOT = "bot_a"


def rec(owner=T22, freeze=None, ext=("metadataPointer", "tokenMetadata"), ok=True):
    return {"fetch_ok": ok, "owner_program": owner, "freeze_authority": freeze, "token2022_extensions": list(ext)}


R4B = {GOOD: rec(), FREEZE: rec(freeze="Frz1"), EXT: rec(ext=("metadataPointer", "transferHook")), FAIL: rec(ok=False), SPLM: rec(owner=SPL, ext=())}


def write_log(root, rows):
    log = DecisionLog(root)
    for i, body in enumerate(rows):
        log.append(BOT, 1_000 + i, body)


def entry(pid, pool, **kw):
    return {"kind": "entry", "order_id": pid, "pool": pool, **kw}


def book(root):
    write_log(root, [
        entry("p1", "pool1", meta={"mint": GOOD}),
        entry("p2", "pool2", meta={"mint": FREEZE}),
        entry("p3", "pool3", meta={"child_mint": EXT}),
        entry("p4", "pool4", card={"mint": FAIL}),
        entry("p5", "pool5", meta={"mint": SPLM}),
        {"kind": "exit", "order_id": "p1", "pool": "pool1", "reason": "tp"},
        entry("p6", "pool6", meta={}),
    ])


@pytest.mark.parametrize("r,why", [
    (rec(), None), (rec(owner=SPL, ext=()), None), (rec(ext=()), None),
    (rec(freeze="X"), "freeze_authority"), (rec(owner=SPL, freeze="X", ext=()), "freeze_authority"),
    (rec(ext=("metadataPointer", "permanentDelegate")), "extension_outside_allowlist"),
    (rec(ok=False), "fetch_failed"), (None, "missing_record"), ({}, "fetch_failed"),
    (rec(owner="Other1111"), "unknown_owner_program"), ({**rec(), "token2022_extensions": None}, "malformed_extensions"),
])
def test_classify(r, why):
    assert C.classify_record(r) == why


def test_ids_and_unresolved(tmp_path):
    book(tmp_path / "ledg")
    ids, s = C.build(BOT, str(tmp_path / "ledg"), R4B)
    assert ids == ["p2", "p3", "p4", "p6"]  # p6 has no mint: unresolved is blocked, never dropped
    assert s["positions"] == 6 and s["unresolved"] == 1 and s["close_blocked_mints"] == 3
    assert s["reasons"] == {"extension_outside_allowlist": 1, "fetch_failed": 1, "freeze_authority": 1, "unresolved_mint": 1}


def test_pool_mints_resolves_and_missing_record_blocks(tmp_path):
    book(tmp_path / "ledg")
    ids, s = C.build(BOT, str(tmp_path / "ledg"), R4B, {"pool6": GOOD})
    assert "p6" not in ids and s["unresolved"] == 0
    ids2, s2 = C.build(BOT, str(tmp_path / "ledg"), {GOOD: rec()}, {"pool6": GOOD})
    assert ids2 == ["p2", "p3", "p4", "p5"] and s2["mints_without_record"] == 4


def test_output_feeds_report_legs_loader(tmp_path):
    book(tmp_path / "ledg")
    (tmp_path / "ma.json").write_text(json.dumps({"fetch_slot": 5, "mints": R4B}))
    args = ["from-r4b", "--bot", BOT, "--decisions-root", str(tmp_path / "ledg"), "--mint-accounts", str(tmp_path / "ma.json"), "--out-dir", str(tmp_path / "o")]
    assert C.main(args) == 0
    assert RL.load_json_ids(str(tmp_path / "o" / "close_blocked.json")) == ["p2", "p3", "p4", "p6"]
    s = json.loads((tmp_path / "o" / "close_blocked_summary.json").read_text())
    assert s["outcome_blind"] is True and s["bot_id"] == BOT


def test_empty_list(tmp_path):
    write_log(tmp_path / "ledg", [entry("p1", "pool1", meta={"mint": GOOD})])
    ids, _ = C.build(BOT, str(tmp_path / "ledg"), R4B)
    assert ids == []


def test_broken_chain_refused(tmp_path):
    book(tmp_path / "ledg")
    f = next((tmp_path / "ledg" / "decisions" / BOT).glob("*.jsonl"))
    lines = f.read_text().splitlines()
    lines[2] = lines[2].replace("pool3", "poolX")
    f.write_text("\n".join(lines) + "\n")
    with pytest.raises(C.Refused, match="chain broken"):
        C.positions_from_declog(tmp_path / "ledg", BOT)
    (tmp_path / "x").write_text(json.dumps({"mints": R4B}))
    args = ["from-r4b", "--bot", BOT, "--decisions-root", str(tmp_path / "ledg"), "--mint-accounts", str(tmp_path / "x"), "--out-dir", str(tmp_path / "o")]
    assert C.main(args) == 3


def test_no_log_refused(tmp_path):
    with pytest.raises(C.Refused):
        C.positions_from_declog(tmp_path, BOT)


@pytest.mark.parametrize("bad", ["sealed", "forward-x", "a_shakedown", "canary", "probe"])
def test_forbidden_parts(tmp_path, bad):
    with pytest.raises(C.Refused):
        C.check_path(tmp_path / bad / "x")
    with pytest.raises(C.Refused):
        C.positions_from_declog(tmp_path / bad, BOT)
    assert C.main(["from-r4b", "--bot", BOT, "--decisions-root", str(tmp_path), "--out-dir", str(tmp_path / bad)]) == 3
    assert C.main(["from-r4b", "--bot", "bot_" + bad, "--decisions-root", str(tmp_path), "--out-dir", str(tmp_path / "o")]) == 3


def test_bad_r4b_schema(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("[]")
    with pytest.raises(C.Refused):
        C.load_r4b(p)


# ---------------------------------------------------------------- fetch wrapper (mocked RPC)
def parsed(owner, info, program):
    return {"context": {"slot": 100}, "value": {"owner": owner, "data": {"program": program, "parsed": {"type": "mint", "info": info}}}}


class Rpc:
    def __init__(self, table, fail_first=()):
        self.table, self.fail_first, self.n = table, set(fail_first), {}
        self.calls = 0

    def __call__(self, url, payload):
        mint = payload["params"][0]
        self.calls += 1
        self.n[mint] = self.n.get(mint, 0) + 1
        if mint in self.fail_first and self.n[mint] <= MF.ATTEMPTS:  # the whole first round fails
            raise OSError("boom https://x?api-key=SECRET")
        return {"result": self.table[mint]}


class Dead(Rpc):
    def __call__(self, url, payload):
        self.calls += 1
        raise OSError("down")


GOOD_INFO = {"mintAuthority": None, "freezeAuthority": None, "decimals": 6, "supply": "5",
             "extensions": [{"extension": "metadataPointer"}, {"extension": "tokenMetadata"}]}
TABLE = {GOOD: parsed(T22, GOOD_INFO, "spl-token-2022"),
         FREEZE: parsed(T22, {**GOOD_INFO, "freezeAuthority": "Frz1"}, "spl-token-2022"),
         FAIL: parsed(T22, GOOD_INFO, "spl-token-2022")}


def fetcher(rpc):
    return MF.Fetcher(MF.rpc_url("KEY1"), post=rpc, sleep=lambda s: None, secrets=["KEY1"])


def test_fetch_records_retries_then_succeeds():
    rpc = Rpc(TABLE, fail_first=[FAIL])
    t = [0.0]
    recs, info = C.fetch_records(fetcher(rpc), [GOOD, FREEZE, FAIL], gap_s=3600, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert info["rounds_used"] == 2 and info["still_failed"] == 0 and info["calls"] == rpc.calls
    assert recs[FAIL]["fetch_ok"] and C.classify_record(recs[FREEZE]) == "freeze_authority"
    assert "SECRET" not in json.dumps(recs)
    doc = C.to_r4b(recs)
    assert doc["fetch_slot"] == 100 and set(doc["mints"][GOOD]) == {"fetch_ok", "owner_program", "freeze_authority", "token2022_extensions"}


def test_fetch_records_window_stops_retries():
    t = [0.0]
    recs, info = C.fetch_records(fetcher(Dead({})), [GOOD], gap_s=10 * 3600, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    # rounds start at 0 h, 10 h, 20 h: all within 24 h, and 3 is the cap
    assert info["rounds_used"] == 3 and info["still_failed"] == 1 and C.classify_record(recs[GOOD]) == "fetch_failed"
    t2 = [0.0]
    _, info2 = C.fetch_records(fetcher(Dead({})), [GOOD], gap_s=13 * 3600, sleep=lambda s: t2.__setitem__(0, t2[0] + s), clock=lambda: t2[0])
    assert info2["rounds_used"] == 2  # a third round would start at 26 h


# ---------------------------------------------------------------- live pre-filter
def test_prefilter_preloaded_cache_and_fetch():
    rpc = Rpc(TABLE)
    t = [0.0]
    pf = C.LivePrefilter(records={FREEZE: rec(freeze="F")}, fetcher=fetcher(rpc), clock=lambda: t[0], ttl_s=100)
    v = pf.check(FREEZE)
    assert (v.blocked, v.reason, v.source, v.calls) == (True, "freeze_authority", "preloaded", 0) and rpc.calls == 0
    v = pf.check(GOOD)
    assert (v.blocked, v.source, v.calls) == (False, "fetch", 1)
    assert pf.check(GOOD).source == "cache" and rpc.calls == 1
    t[0] = 101
    assert pf.check(GOOD).source == "fetch" and rpc.calls == 2


def test_prefilter_failure_denies_and_retries_after_short_ttl():
    rpc = Dead({})
    t = [0.0]
    pf = C.LivePrefilter(fetcher=fetcher(rpc), clock=lambda: t[0], fail_ttl_s=30)
    assert pf.check(GOOD).reason == "fetch_failed"
    n = rpc.calls
    assert pf.check(GOOD).source == "cache" and rpc.calls == n
    t[0] = 31
    assert pf.check(GOOD).source == "fetch" and rpc.calls > n


def test_prefilter_no_mint_no_fetcher_deny():
    pf = C.LivePrefilter()
    assert pf.check(None).blocked and pf.check("").reason == "unresolved_mint"
    assert pf.check(GOOD).reason == "missing_record"


def test_mint_account_fetch_untouched():
    assert MF.ATTEMPTS == 3 and MF.MAX_RPS == 10.0 and callable(MF.fetch_one)
