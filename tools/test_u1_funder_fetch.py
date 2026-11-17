"""u1_funder_fetch: inflow parsing fixtures, credit cap refusal, key never logged, stored keys, hub test, pilot record shape. Mocked RPC, no network, no real address or key."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import u1_funder_fetch as F  # noqa: E402

L = "LauncherAAA"
FUNDER = "FunderBBB"
SECRET = "KEY-SECRET-12345"


def sys_ins(typ, **info):
    return {"program": "system", "programId": F.SYSTEM_PROGRAM, "parsed": {"type": typ, "info": info}}


def tx(ins, inner=(), slot=100, bt=1_000_000):
    return {"slot": slot, "blockTime": bt, "transaction": {"message": {"instructions": list(ins)}}, "meta": {"innerInstructions": [{"index": 0, "instructions": list(inner)}] if inner else []}}


class Mock:
    """routes by method; sigs[address] = list of pages (newest-first entries); txs[signature] = tx dict"""

    def __init__(self, sigs=None, txs=None, fail=None):
        self.sigs, self.txs, self.fail, self.log = sigs or {}, txs or {}, fail or {}, []

    def __call__(self, method, params):
        self.log.append((method, params))
        if method == "getSignaturesForAddress":
            addr, opts = params
            pages = self.sigs.get(addr, [[]])
            idx = 0
            if opts.get("before"):
                idx = next((i + 1 for i, p in enumerate(pages) if p and p[-1]["signature"] == opts["before"]), len(pages) - 1)
                idx = min(idx, len(pages) - 1)
            return pages[idx]
        if method == "getTransaction":
            if params[0] in self.fail:
                raise F.RpcError(self.fail[params[0]], retryable=self.fail[params[0]] in (429, 500))
            return self.txs.get(params[0])
        raise AssertionError("method outside the allowed two: %s" % method)


def sig(n, err=None, bt=1000):
    return {"signature": "s%d" % n, "slot": n, "blockTime": bt, "err": err}


def fetcher(rpc, cap=1000):
    return F.Fetcher(rpc, F.CreditMeter(cap), sleep_fn=lambda s: None, clock=lambda: 0.0)


# ---------------------------------------------------------------------------------------------------------------------------- inflow fixtures
def test_transfer_counts():
    assert F.qualifying_inflow(tx([sys_ins("transfer", source=FUNDER, destination=L, lamports=5_000_000)]), L) == (FUNDER, 5_000_000)


def test_transfer_with_seed_and_create_account_count():
    assert F.qualifying_inflow(tx([sys_ins("transferWithSeed", source="X", destination=L, lamports=2_000_000)]), L) == ("X", 2_000_000)
    assert F.qualifying_inflow(tx([sys_ins("createAccount", source="Y", newAccount=L, lamports=3_000_000)]), L) == ("Y", 3_000_000)


def test_inner_cpi_transfer_counts_and_largest_wins():
    t = tx([sys_ins("transfer", source="small", destination=L, lamports=1_500_000)], inner=[sys_ins("transfer", source="big", destination=L, lamports=9_000_000)])
    assert F.qualifying_inflow(t, L) == ("big", 9_000_000)


def test_close_account_and_other_programs_do_not_count():
    close = {"program": "spl-token", "programId": "Tokenkeg", "parsed": {"type": "closeAccount", "info": {"account": "a", "destination": L, "owner": "o"}}}
    fake = {"program": "other", "programId": "Prog", "parsed": {"type": "transfer", "info": {"source": "s", "destination": L, "lamports": 9_000_000}}}
    assert F.qualifying_inflow(tx([close, fake]), L) is None


def test_dust_and_other_destination_skipped():
    assert F.qualifying_inflow(tx([sys_ins("transfer", source=FUNDER, destination=L, lamports=999_999)]), L) is None
    assert F.qualifying_inflow(tx([sys_ins("transfer", source=FUNDER, destination="someone", lamports=9_000_000)]), L) is None
    assert F.qualifying_inflow(tx([sys_ins("transfer", source=FUNDER, destination=L, lamports=True)]), L) is None


# ---------------------------------------------------------------------------------------------------------------------------- one launcher
def test_resolved_uses_oldest_successful_and_stores_only_keys():
    m = Mock(sigs={L: [[sig(3), sig(2, err={"x": 1}), sig(1)]]}, txs={"s1": tx([sys_ins("transfer", source=FUNDER, destination=L, lamports=4_000_000)], slot=50)})
    f = fetcher(m)
    row = f.fetch_launcher(L, 100)
    assert row == {"launcher": L, "funder": FUNDER, "funding_slot": 50, "status": "resolved"}
    assert [c[1][0] for c in m.log if c[0] == "getTransaction"] == ["s1"]  # the failed signature is never fetched
    assert set(f.table_a_rows()[0]) == set(F.TABLE_A_KEYS)


def test_next_oldest_up_to_three_transactions_then_no_sol_inflow():
    sigs = [sig(i) for i in range(5, 0, -1)]
    m = Mock(sigs={L: [sigs]}, txs={"s%d" % i: tx([]) for i in range(1, 6)})
    f = fetcher(m)
    assert f.fetch_launcher(L, 100)["status"] == "no_sol_inflow"
    assert [c[1][0] for c in m.log if c[0] == "getTransaction"] == ["s1", "s2", "s3"]


def test_funding_at_or_after_first_create_slot_is_funding_after_create():
    m = Mock(sigs={L: [[sig(1)]]}, txs={"s1": tx([sys_ins("transfer", source=FUNDER, destination=L, lamports=4_000_000)], slot=100)})
    row = fetcher(m).fetch_launcher(L, 100)
    assert row["status"] == "funding_after_create" and row["funder"] is None and row["funding_slot"] is None


def test_deep_after_20_pages():
    page = lambda k: [sig(k * 1000 + i) for i in range(1000, 0, -1)]
    m = Mock(sigs={L: [page(k) for k in range(30)]})
    f = fetcher(m)
    assert f.fetch_launcher(L, 100)["status"] == "deep"
    assert f.pages_per_launcher[L] == F.MAX_PAGES and not [c for c in m.log if c[0] == "getTransaction"]


def test_retry_then_fetch_error_counts_every_request():
    m = Mock(sigs={L: [[sig(1)]]}, fail={"s1": 429})
    f = fetcher(m)
    assert f.fetch_launcher(L, 100)["status"] == "fetch_error"
    assert f.counters["retries"] == 3 and f.counters["n429"] == 4
    assert f.meter.used == 1 + 4  # one page + 4 attempts


def test_version_1_transaction_resolves_and_request_carries_version_1():
    t = tx([sys_ins("transfer", source=FUNDER, destination=L, lamports=4_000_000)], slot=50)
    t["version"] = 1
    m = Mock(sigs={L: [[sig(1)]]}, txs={"s1": t})
    row = fetcher(m).fetch_launcher(L, 100)
    assert row["status"] == "resolved" and row["funder"] == FUNDER
    assert F.MAX_TX_VERSION == 1
    gt = [c[1][1] for c in m.log if c[0] == "getTransaction"]
    assert gt and all(o["maxSupportedTransactionVersion"] == 1 and o["encoding"] == "jsonParsed" for o in gt)


def test_rpc_32015_is_fetch_error_and_counted_by_code():
    m = Mock(sigs={L: [[sig(1)]]}, fail={"s1": -32015})
    f = fetcher(m)
    assert f.fetch_launcher(L, 100)["status"] == "fetch_error"
    assert dict(f.rpc_errors) == {("getTransaction", -32015, False): 1}
    assert f.rpc_errors_by_key() == {"getTransaction|-32015|false": 1}
    assert F.pilot_record(f, 1000, 1.0)["rpc_errors"] == {"getTransaction|-32015|false": 1}


def test_rpc_errors_keyed_by_method_code_retryable():
    m = Mock(sigs={L: [[sig(1)]]}, fail={"s1": 429})
    f = fetcher(m)
    f.fetch_launcher(L, 100)
    assert f.rpc_errors_by_key() == {"getTransaction|429|true": 4}


def test_summary_of_every_mode_carries_rpc_errors(tmp_path, capsys, monkeypatch):
    import inspect
    src = inspect.getsource(F.main)
    assert src.index('"rpc_errors": f.rpc_errors_by_key()') < src.index('summary["pilot_record"]')  # in the base summary, not only the pilot record


def _v1(ins, inner=()):
    t = tx(ins, inner=inner, slot=50)
    t["version"] = 1
    return t


def _resolve_v1(t):
    m = Mock(sigs={L: [[sig(1)]]}, txs={"s1": t})
    return fetcher(m).fetch_launcher(L, 100)["status"]


def test_v1_inner_instruction_transfer_resolves():
    assert _resolve_v1(_v1([], inner=[sys_ins("transfer", source=FUNDER, destination=L, lamports=5_000_000)])) == "resolved"


def test_v1_create_account_resolves():
    assert _resolve_v1(_v1([sys_ins("createAccount", source=FUNDER, newAccount=L, lamports=5_000_000)])) == "resolved"


def test_v1_dust_transfer_does_not_count():
    assert _resolve_v1(_v1([sys_ins("transfer", source=FUNDER, destination=L, lamports=999_999)])) == "no_sol_inflow"


def test_v1_nonce_withdrawal_does_not_count():
    assert _resolve_v1(_v1([sys_ins("withdrawFromNonce", account="N", destination=L, lamports=5_000_000, nonceAuthority="A")])) == "no_sol_inflow"


# ---------------------------------------------------------------------------------------------------------------------------- credit cap
def test_cap_refuses_before_the_call_and_marks_fetch_capped():
    m = Mock(sigs={"L%d" % i: [[sig(1)]] for i in range(10)}, txs={"s1": tx([sys_ins("transfer", source=FUNDER, destination="x", lamports=4_000_000)])})
    f = fetcher(m, cap=5)
    f.run([("L%d" % i, 100) for i in range(10)])
    assert f.meter.used <= 5 and len(m.log) <= 5  # never a call past the cap
    st = [r["status"] for r in f.table_a.values()]
    assert "fetch_capped" in st and st.count("fetch_capped") >= 5


def test_meter_never_exceeds_cap_and_unknown_method_refused():
    mt = F.CreditMeter(3)
    for _ in range(3):
        mt.charge("getTransaction")
    with pytest.raises(F.CapReached):
        mt.charge("getTransaction")
    assert mt.used == 3
    with pytest.raises(KeyError):
        mt.charge("getTransactionsForAddress")


def test_caps_match_ruling():
    assert F.CAPS == {"pilot": 100, "backfill": 30_000, "forward_day": 3_000}


def test_forward_cap_is_per_utc_day():
    days = ["d1"]
    dm = F.DailyMeters(cap_per_day=2)
    f = F.Fetcher(Mock(sigs={"A": [[]], "B": [[]], "C": [[]]}), dm, sleep_fn=lambda s: None, clock=lambda: 0.0, day_fn=lambda: days[0])
    f.fetch_launcher("A", 1)
    f.fetch_launcher("B", 1)
    assert f.fetch_launcher("C", 1)["status"] == "fetch_capped"  # d1 cap reached
    days[0] = "d2"
    assert f.fetch_launcher("D", 1)["status"] == "no_sol_inflow"  # a new day, a new budget
    assert dm.total_used() == 3


# ---------------------------------------------------------------------------------------------------------------------------- key
def test_key_never_in_log_output_or_exception(tmp_path, capsys):
    env = tmp_path / "h.env"
    env.write_text("HELIUS_API_KEY=%s\n" % SECRET)
    assert F.load_key(str(env)) == SECRET
    env2 = tmp_path / "h2.env"
    env2.write_text("HELIUS_RPC_URL=https://x/?api-key=%s\n" % SECRET)
    assert F.load_key(str(env2)) == SECRET

    # the transport's errors drop the URL
    import urllib.request
    def boom(req, timeout=None):
        raise OSError("connect failed to %s" % req.full_url)
    orig = urllib.request.urlopen
    urllib.request.urlopen = boom
    try:
        with pytest.raises(F.RpcError) as ei:
            F.make_transport(SECRET)("getTransaction", ["s"])
    finally:
        urllib.request.urlopen = orig
    assert SECRET not in str(ei.value) and SECRET not in repr(ei.value) and ei.value.__cause__ is None

    # missing key file: the error text holds neither the key nor the path
    with pytest.raises(F.KeyError_) as ke:
        F.load_key(str(tmp_path / "nope.env"))
    assert "nope" not in str(ke.value)

    # CLI: a pilot run on a mocked transport prints counts only
    ll = tmp_path / "l.jsonl"
    ll.write_text(json.dumps({"launcher": L, "first_create_slot": 100}) + "\n")
    import unittest.mock as um
    with um.patch.object(F, "make_transport", lambda key: Mock(sigs={L: [[sig(1)]]}, txs={"s1": tx([sys_ins("transfer", source=FUNDER, destination=L, lamports=4_000_000)], slot=5)})):
        rc = F.main(["pilot", "--launchers", str(ll), "--out", str(tmp_path / "o"), "--env-file", str(env), "--n-w", "100"])
    out = capsys.readouterr()
    assert rc == 0 and SECRET not in out.out + out.err and L not in out.out + out.err and FUNDER not in out.out + out.err


def test_cli_refuses_without_key_file(tmp_path, capsys):
    ll = tmp_path / "l.jsonl"
    ll.write_text("")
    assert F.main(["pilot", "--launchers", str(ll), "--out", str(tmp_path / "o"), "--env-file", str(tmp_path / "missing")]) == 2


# ---------------------------------------------------------------------------------------------------------------------------- hub test
def test_hub_test_only_on_second_launcher_and_is_past_only():
    full = [{"signature": "f%d" % i, "slot": i, "blockTime": 10_000 - i // 100, "err": None} for i in range(1000)]  # oldest entry is 10 s before 10_000
    funding = lambda dst: tx([sys_ins("transfer", source=FUNDER, destination=dst, lamports=2_000_000)], slot=5, bt=10_000)
    m = Mock(sigs={"L1": [[sig(1)]], "L2": [[sig(2)]], FUNDER: [full]}, txs={"s1": funding("L1"), "s2": funding("L2")})
    f = fetcher(m)
    f.fetch_launcher("L1", 100)
    assert f.counters["hub_tests"] == 0 and FUNDER not in f.table_b
    f.fetch_launcher("L2", 100)
    assert f.counters["hub_tests"] == 1 and f.table_b == {FUNDER: True}
    hub_calls = [c for c in m.log if c[0] == "getSignaturesForAddress" and c[1][0] == FUNDER]
    assert len(hub_calls) == 1 and hub_calls[0][1][1]["before"] == "s2" and hub_calls[0][1][1]["limit"] == 1000
    f.fetch_launcher("L3", 100)  # no more tests for a tested funder
    assert f.counters["hub_tests"] == 1
    assert f.table_b_rows() == [{"funder": FUNDER, "hub_activity": True}]


def test_hub_test_false_when_page_short_or_old():
    short = [sig(i, bt=9000) for i in range(10)]
    old = [{"signature": "o%d" % i, "slot": i, "blockTime": 1, "err": None} for i in range(1000)]
    for page in (short, old):
        funding = lambda dst: tx([sys_ins("transfer", source=FUNDER, destination=dst, lamports=2_000_000)], slot=5, bt=10_000)
        m = Mock(sigs={"L1": [[sig(1)]], "L2": [[sig(2)]], FUNDER: [page]}, txs={"s1": funding("L1"), "s2": funding("L2")})
        f = fetcher(m)
        f.fetch_launcher("L1", 100)
        f.fetch_launcher("L2", 100)
        assert f.table_b == {FUNDER: False}


# ---------------------------------------------------------------------------------------------------------------------------- tables and pilot
def test_tables_written_0440_with_exact_keys_and_resume(tmp_path):
    m = Mock(sigs={L: [[sig(1)]]}, txs={"s1": tx([sys_ins("transfer", source=FUNDER, destination=L, lamports=4_000_000)], slot=5)})
    f = fetcher(m)
    f.fetch_launcher(L, 100)
    shas = F.write_tables(f, tmp_path)
    for name in ("table_a.jsonl", "table_b.jsonl"):
        p = tmp_path / name
        assert oct(p.stat().st_mode & 0o777) == "0o440" and hashlib.sha256(p.read_bytes()).hexdigest() == shas[name]
    rows = [json.loads(x) for x in (tmp_path / "table_a.jsonl").read_text().splitlines()]
    assert rows == [{"launcher": L, "funder": FUNDER, "funding_slot": 5, "status": "resolved"}]
    F.write_tables(f, tmp_path)  # overwrite of a 0440 file works (atomic replace)
    a, b = F.load_tables(tmp_path)
    assert a[L]["funder"] == FUNDER and b == {}


def test_pilot_selection_is_the_ten_smallest_sha256():
    ls = [("addr%d" % i, i) for i in range(50)]
    sel = F.pilot_selection(ls)
    assert len(sel) == 10
    hs = sorted(hashlib.sha256(a.encode()).hexdigest() for a, _ in ls)[:10]
    assert [hashlib.sha256(a.encode()).hexdigest() for a, _ in sel] == hs
    assert F.pilot_selection(list(reversed(ls))) == sel  # input order does not matter


def test_pilot_record_shape_and_verdict():
    sigs, txs = {}, {}
    for i in range(10):
        a = "W%d" % i
        sigs[a] = [[sig(i + 1)]]
        txs["s%d" % (i + 1)] = tx([sys_ins("transfer", source="Fx%d" % i, destination=a, lamports=3_000_000)], slot=5)
    f = F.Fetcher(Mock(sigs=sigs, txs=txs), F.CreditMeter(F.CAPS["pilot"]), sleep_fn=lambda s: None, clock=lambda: 0.0)
    f.run([("W%d" % i, 100) for i in range(10)])
    rec = F.pilot_record(f, n_w=1000, wall_s=1.5)
    for k in ("status_counts", "pages_per_launcher_mean", "pages_per_launcher_max", "get_transaction_per_launcher_mean", "get_transaction_per_launcher_max", "hub_tests", "credits_table_total",
              "credits_table_mean_per_launcher", "dashboard_note", "retries", "n429", "wall_time_s", "n_funding_after_create", "stored_keys_exact", "pass", "verdict", "projected_backfill_credits"):
        assert k in rec
    assert rec["status_counts"]["resolved"] == 10 and rec["credits_table_total"] == 20 and rec["credits_table_mean_per_launcher"] == 2.0
    assert rec["projected_backfill_credits"] == 1000 * 2.0 + 500 and rec["verdict"] == "PASS" and rec["stored_keys_exact"]
    assert rec["dashboard_note"] == "dashboard delta not available"
    assert "W0" not in json.dumps(rec) and "Fx0" not in json.dumps(rec)  # counts only
    assert F.pilot_record(f, n_w=20_000, wall_s=1)["verdict"] == "FAIL_CAP_ONLY (a)"
    assert F.pilot_record(f, n_w=1000, wall_s=1, dashboard_delta=100)["verdict"] == "BLOCKED"  # (e): dashboard 5x the table


def test_pilot_blocked_when_fewer_than_five_resolved():
    f = F.Fetcher(Mock(sigs={"A%d" % i: [[]] for i in range(10)}), F.CreditMeter(100), sleep_fn=lambda s: None, clock=lambda: 0.0)
    f.run([("A%d" % i, 100) for i in range(10)])
    rec = F.pilot_record(f, 100, 1)
    assert rec["verdict"] == "BLOCKED" and not rec["pass"]["b_resolved_ge_5"]


def test_backfill_cap_flag_is_lower_only_and_backfill_only(tmp_path, capsys):
    lst = tmp_path / "l.jsonl"
    lst.write_text("")
    for argv in (["backfill", "--cap", "30001"], ["backfill", "--cap", "-1"], ["pilot", "--cap", "50"], ["forward", "--cap", "5"]):
        assert F.main(argv + ["--launchers", str(lst), "--out", str(tmp_path / "o"), "--env-file", str(tmp_path / "none.env")]) == 2
    assert "--cap" in capsys.readouterr().err


def test_day_meter_seeded_from_stored_use_and_hook_fires_per_charge():
    seen = []
    dm = F.DailyMeters(5, used_by_day={"d": 3}, on_charge=lambda: seen.append(1))
    m = dm.for_day("d")
    m.charge("getTransaction")
    m.charge("getTransaction")
    with pytest.raises(F.CapReached):
        m.charge("getTransaction")
    assert dm.used_by_day() == {"d": 5} and len(seen) == 2 and F.FORWARD_RPS == 2.0
