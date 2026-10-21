"""Tests for tools/arena_close_blocked.py. Fixtures only: a mocked RPC, a decision log written with DecisionLog. No network, no env file."""

from __future__ import annotations

import json

import pytest

from tools import arena_close_blocked as C
from tools import arena_report_legs as RL
from tools import mint_account_fetch as MF
from tools import test_arena_report_legs as T
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
    (rec(owner="Other1111"), "unknown_owner_program"), ({**rec(), "token2022_extensions": None}, "malformed_record"),
    ({"fetch_ok": True, "owner_program": SPL, "token2022_extensions": []}, "malformed_record"),  # N2: no freeze_authority key
    ({"fetch_ok": True, "owner_program": SPL, "freeze_authority": None}, "malformed_record"),  # no extensions list
    ({"fetch_ok": True, "owner_program": T22, "freeze_authority": 5, "token2022_extensions": []}, "malformed_record"),
])
def test_classify(r, why):
    assert C.classify_record(r) == why


def pos_of(root, **kw):
    return C.positions_from_declog(root, BOT, **kw)


def test_ids_and_unresolved(tmp_path):
    book(tmp_path / "ledg")
    positions, dropped = pos_of(tmp_path / "ledg")
    ids, s = C.build(positions, R4B, BOT)
    assert ids == ["p2", "p3", "p4", "p6"]  # p6 has no mint: unresolved is blocked, never dropped
    assert s["positions"] == 6 and s["unresolved"] == 1 and s["close_blocked_mints"] == 3 and dropped["refused"] == 0
    assert s["reasons"] == {"extension_outside_allowlist": 1, "fetch_failed": 1, "freeze_authority": 1, "unresolved_mint": 1}


def test_pool_mints_resolves_and_missing_record_blocks(tmp_path):
    book(tmp_path / "ledg")
    positions, _ = pos_of(tmp_path / "ledg")
    _, pos = C.traded_mints(positions, {"pool6": GOOD})
    ids, s = C.build(pos, R4B)
    assert "p6" not in ids and s["unresolved"] == 0
    ids2, s2 = C.build(pos, {GOOD: rec()})
    assert ids2 == ["p2", "p3", "p4", "p5"] and s2["mints_without_record"] == 4


def test_output_feeds_report_legs_loader(tmp_path):
    book(tmp_path / "ledg")
    (tmp_path / "ma.json").write_text(json.dumps({"fetch_slot": 5, "mints": R4B}))
    args = ["from-r4b", "--bot", BOT, "--decisions-root", str(tmp_path / "ledg"), "--mint-accounts", str(tmp_path / "ma.json"), "--out-dir", str(tmp_path / "o")]
    assert C.main(args) == 0
    assert RL.load_json_ids(str(tmp_path / "o" / "close_blocked.json")) == ["p2", "p3", "p4", "p6"]
    s = json.loads((tmp_path / "o" / "close_blocked_summary.json").read_text())
    assert s["outcome_blind"] is True and s["bot_id"] == BOT and s["covered_set"] == "not applied"


def test_empty_list(tmp_path):
    write_log(tmp_path / "ledg", [entry("p1", "pool1", meta={"mint": GOOD})])
    positions, _ = pos_of(tmp_path / "ledg")
    assert C.build(positions, R4B)[0] == []


def test_refused_and_window_filters(tmp_path):
    write_log(tmp_path / "ledg", [
        entry("early", "pa", meta={"mint": FREEZE}),       # ms 1000
        entry("ok", "pb", meta={"mint": FREEZE}),          # ms 1001
        entry("refd", "pc", meta={"mint": FREEZE}),        # ms 1002
        {"kind": "order_refused", "order_id": "refd", "side": "buy", "reason": "x"},
        entry("late", "pd", meta={"mint": FREEZE}),        # ms 1004
    ])
    positions, dropped = pos_of(tmp_path / "ledg", clock_ms=1001, boundary_ms=1004)
    assert [p["position_id"] for p in positions] == ["ok"]
    assert dropped == {"refused": 1, "before_clock": 1, "at_or_after_boundary": 1}


def test_covered_intersection():
    pos = [{"position_id": "a"}, {"position_id": "b"}]
    assert C.select_positions(pos, {"a"}) == ([{"position_id": "a"}], 1)
    assert C.select_positions(pos, None) == (pos, 0)


def test_hv4_arm_name_accepted(tmp_path):
    log = DecisionLog(tmp_path / "ledg")
    log.append("CON-L", 1000, entry("h1", "pool", meta={"mint": GOOD}))
    for name in ("CON-L", "hv4-CON-L"):
        assert [p["position_id"] for p in C.positions_from_declog(tmp_path / "ledg", name)[0]] == ["h1"]
    with pytest.raises(C.Refused):
        C.positions_from_declog(tmp_path / "ledg", "hv4-OTHER")


def test_broken_chain_refused(tmp_path):
    book(tmp_path / "ledg")
    f = next((tmp_path / "ledg" / "decisions" / BOT).glob("*.jsonl"))
    lines = f.read_text().splitlines()
    lines[2] = lines[2].replace("pool3", "poolX")
    f.write_text("\n".join(lines) + "\n")
    with pytest.raises(C.Refused, match="chain broken"):
        pos_of(tmp_path / "ledg")
    (tmp_path / "x").write_text(json.dumps({"mints": R4B}))
    args = ["from-r4b", "--bot", BOT, "--decisions-root", str(tmp_path / "ledg"), "--mint-accounts", str(tmp_path / "x"), "--out-dir", str(tmp_path / "o")]
    assert C.main(args) == 3


def test_torn_last_line_refused(tmp_path):
    book(tmp_path / "ledg")
    f = next((tmp_path / "ledg" / "decisions" / BOT).glob("*.jsonl"))
    f.write_bytes(f.read_bytes().rstrip(b"\n"))
    with pytest.raises(C.Refused, match="torn"):
        pos_of(tmp_path / "ledg")


def test_no_log_refused(tmp_path):
    with pytest.raises(C.Refused):
        pos_of(tmp_path)


@pytest.mark.parametrize("bad", ["sealed", "forward-x", "a_shakedown", "canary", "probe"])
def test_forbidden_parts(tmp_path_factory, bad):
    # N1: pytest names tmp_path after the test and its parameter, so the base comes from tmp_path_factory with a neutral name
    base = tmp_path_factory.mktemp("neutral")
    book(base / "ledg")
    (base / "x").write_text(json.dumps({"mints": R4B}))
    ok = ["from-r4b", "--bot", BOT, "--decisions-root", str(base / "ledg"), "--mint-accounts", str(base / "x"), "--out-dir", str(base / "o")]
    C.check_path(base / "ledg")  # a neutral path passes
    assert C.main(ok) == 0
    with pytest.raises(C.Refused):
        C.check_path(base / bad / "x")
    with pytest.raises(C.Refused):
        pos_of(base / bad)
    assert C.main([*ok[:-1], str(base / bad)]) == 3
    assert C.main([ok[0], "--bot", "bot_" + bad, *ok[3:]]) == 3
    assert C.main([*ok[:-4], "--mint-accounts", str(base / bad / "x"), "--out-dir", str(base / "o2")]) == 3


def test_bad_r4b_schema(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("[]")
    with pytest.raises(C.Refused):
        C.load_r4b(p)


def test_fetch_mode_needs_env_file(tmp_path):
    book(tmp_path / "ledg")
    assert C.main(["fetch", "--bot", BOT, "--decisions-root", str(tmp_path / "ledg"), "--out-dir", str(tmp_path / "o")]) == 3
    assert not (tmp_path / "o").exists()


# ---------------------------------------------------------------- END TO END through arena_report_legs (B1)
def e2e_world(tmp_path):
    rows, blocked, _, _ = T.hundred_trips()
    root, reg = T.write_world(tmp_path, rows)
    bad = "FRZMINT1111111111111111111111111111111111111"
    recs = {bad: rec(freeze="Frz1")}
    ev = []  # (ms, body); a real log is chronological, so it is appended in time order
    for r in rows:
        if r["kind"] == "open":
            m = bad if r["position_id"] in blocked else "M" + r["position_id"]
            recs[m] = recs.get(m) or rec()
            ev.append((r["decision_ms"]["entry"], entry(r["position_id"], "pool-" + r["position_id"], meta={"mint": m})))
    ev.append((T.DAY0 + 2000, entry("p_refused", "pool-r", meta={"mint": bad})))  # entry, then refused: no position
    ev.append((T.DAY0 + 2001, {"kind": "order_refused", "order_id": "p_refused", "side": "buy", "reason": "x"}))
    ev.append((T.NOW + 5 * T.DAY, entry("p_post", "pool-p", meta={})))  # after the boundary, no mint
    ev.append((T.DAY0 + 3000, entry("p_nofill", "pool-n", meta={"mint": bad})))  # in the window, never in the ledger
    log = DecisionLog(tmp_path / "decl")
    for ms, body in sorted(ev, key=lambda e: e[0]):
        log.append("botA", ms, body)
    return root, reg, blocked, recs


def test_end_to_end_list_is_accepted_by_the_read_and_the_naive_list_is_not(tmp_path):
    root, reg, blocked, recs = e2e_world(tmp_path)
    (tmp_path / "ma.json").write_text(json.dumps({"fetch_slot": 1, "mints": recs}))
    naive_positions, _ = C.positions_from_declog(tmp_path / "decl", "botA")
    naive, _ = C.build(naive_positions, recs)
    assert {"p_post", "p_nofill"} <= set(naive) and "p_refused" not in set(naive)  # refused entries are dropped at the source; the other two need the covered set
    with pytest.raises(T.Refused, match="not in this book's covered trade set"):
        T.R.build_report("botA", root, reg, now_ms=T.NOW, close_blocked=naive)
    args = ["from-r4b", "--bot", "botA", "--decisions-root", str(tmp_path / "decl"), "--mint-accounts", str(tmp_path / "ma.json"), "--out-dir", str(tmp_path / "o"),
            "--ledger-root", root, "--registry", reg, "--now-ms", str(T.NOW)]
    assert C.main(args) == 0
    ids = RL.load_json_ids(str(tmp_path / "o" / "close_blocked.json"))
    assert ids == sorted(blocked)
    rep = T.R.build_report("botA", root, reg, now_ms=T.NOW, close_blocked=ids)  # does not refuse
    assert rep["close_blocked"] == {"supplied": True, "listed": 4} and rep["books"]["0.25"]["n_close_blocked"] == 4
    s = json.loads((tmp_path / "o" / "close_blocked_summary.json").read_text())
    assert s["dropped_entries"]["refused"] == 1 and s["dropped_entries"]["not_covered"] >= 2


def test_end_to_end_with_covered_ids_file_and_boundary(tmp_path):
    root, reg, blocked, recs = e2e_world(tmp_path)
    (tmp_path / "ma.json").write_text(json.dumps({"fetch_slot": 1, "mints": recs}))
    covered = C.covered_ids_from_ledger("botA", root, reg, T.NOW)
    assert len(covered) == 100 and "p_refused" not in covered
    (tmp_path / "cov.json").write_text(json.dumps(sorted(covered)))
    args = ["from-r4b", "--bot", "botA", "--decisions-root", str(tmp_path / "decl"), "--mint-accounts", str(tmp_path / "ma.json"), "--out-dir", str(tmp_path / "o"),
            "--covered-ids", str(tmp_path / "cov.json"), "--boundary-ms", str(T.NOW), "--clock-ms", str(T.DAY0)]
    assert C.main(args) == 0
    ids = RL.load_json_ids(str(tmp_path / "o" / "close_blocked.json"))
    assert ids == sorted(blocked)
    T.R.build_report("botA", root, reg, now_ms=T.NOW, close_blocked=ids)
    assert C.main([*args, "--ledger-root", root, "--registry", reg]) == 3  # both sources: refused


# ---------------------------------------------------------------- fetch wrapper (mocked RPC)
def parsed(owner, info, program):
    return {"context": {"slot": 100}, "value": {"owner": owner, "data": {"program": program, "parsed": {"type": "mint", "info": info}}}}


class Rpc:
    """Fails the first `fail` calls of a mint with a transport error (an OSError), then answers from the table (or with a fixed `reply`)."""

    def __init__(self, table, fail=0, reply=None):
        self.table, self.fail, self.reply, self.n = table, fail, reply, {}
        self.calls = 0

    def __call__(self, url, payload):
        mint = payload["params"][0]
        self.calls += 1
        self.n[mint] = self.n.get(mint, 0) + 1
        if self.n[mint] <= self.fail:
            raise OSError("boom https://x?api-key=SECRET")
        if self.reply is not None:
            return self.reply
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
    return C.OneShotFetcher(MF.rpc_url("KEY1"), post=rpc, sleep=lambda s: None, secrets=["KEY1"])


def clocked():
    t = [0.0]
    return (lambda s: t.__setitem__(0, t[0] + s)), (lambda: t[0])


def test_fetch_retries_transport_then_succeeds():
    rpc = Rpc(TABLE, fail=2)
    sl, ck = clocked()
    recs, info = C.fetch_records(fetcher(rpc), [GOOD, FREEZE], sleep=sl, clock=ck)
    assert recs[GOOD]["fetch_ok"] and C.classify_record(recs[FREEZE]) == "freeze_authority"
    assert info["tries"] == {GOOD: 3, FREEZE: 3} and info["still_failed"] == 0 and info["calls"] == rpc.calls == 2 * 3
    assert "SECRET" not in json.dumps(recs)
    doc = C.to_r4b(recs)
    assert doc["fetch_slot"] == 100 and set(doc["mints"][GOOD]) == {"fetch_ok", "owner_program", "freeze_authority", "token2022_extensions"}


def test_exactly_one_try_plus_three_retries_per_mint():
    rpc = Dead({})
    sl, ck = clocked()
    recs, info = C.fetch_records(fetcher(rpc), [GOOD], sleep=sl, clock=ck)
    assert info["tries"] == {GOOD: 4} and rpc.calls == 4 and info["rounds_used"] == 4  # 1 + 3, one call per try (the jsonParsed call raises first)
    assert C.classify_record(recs[GOOD]) == "fetch_failed" and info["still_failed"] == 1
    rpc = Rpc(TABLE, fail=3)  # the 4th try succeeds
    recs, info = C.fetch_records(fetcher(rpc), [GOOD], sleep=sl, clock=ck)
    assert recs[GOOD]["fetch_ok"] and info["tries"] == {GOOD: 4}


@pytest.mark.parametrize("reply", [{"error": {"code": -32005}}, {"nope": 1}, {"result": {"value": None, "context": {"slot": 1}}}])
def test_non_transport_failures_deny_at_once(reply):
    rpc = Rpc({}, reply=reply)
    sl, ck = clocked()
    recs, info = C.fetch_records(fetcher(rpc), [GOOD], sleep=sl, clock=ck)
    assert info["tries"] == {GOOD: 1} and rpc.calls == 1 and C.classify_record(recs[GOOD]) == "fetch_failed"


def test_retry_window_24h():
    sl, ck = clocked()
    _, info = C.fetch_records(fetcher(Dead({})), [GOOD], gap_s=10 * 3600, sleep=sl, clock=ck)
    assert info["tries"] == {GOOD: 3}  # tries at 0 h, 10 h, 20 h; a 4th would start at 30 h
    sl, ck = clocked()
    _, info = C.fetch_records(fetcher(Dead({})), [GOOD], gap_s=13 * 3600, sleep=sl, clock=ck)
    assert info["tries"] == {GOOD: 2}


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
