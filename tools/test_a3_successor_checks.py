"""Tests for the SUCCESSOR-book parts of tools/a3_redeploy_check.py (QP-RULING-1024-redeploy item (2) and the job #1545 enumeration finding): the opt-in fees-program
check (`fees_check` sub-verdict) and the bounded upgrade-write enumeration. No network: fake RPC client and fake HTTP, reusing the fixtures of test_a3_redeploy_check.py
(imported by name; its tests are not re-collected here). The existing test file is unchanged."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
from pathlib import Path

import pytest

from observe import trade_decode as td
from tools import a3_redeploy_check as c
from tools import pump_structure_monitor as mon
from tools.test_a3_redeploy_check import (DEPLOY_PUMP, MON_PINS, NOW, FakeClient, build_pins, encode, flag, http_ok, make_rec, ms, sample_blobs)

FEES_DEPLOY = "2026-10-20T01:00:00Z"
FEES_RUN = "2026-10-20T07:11:27Z"
FEECFG = "FeeCfg1111111111111111111111111111111111111"
AUTH = "7gZufwwAo17y5kg8FMyJy2phgpvv9RSdzWtdXiWHjFr8"
SHIPPED = json.loads(Path(c.DEFAULT_FEES_PINS).read_text())
TIERS = SHIPPED["canonical_tiers"]


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(c, "RETRY_SLEEP", lambda s: None)


def fee_config_bytes(tiers=TIERS) -> bytes:
    out = bytearray(b"\x00" * 8 + b"\x01" + b"\x00" * 32 + struct.pack("<3Q", 25, 5, 0) + struct.pack("<I", len(tiers)))
    for thr, bps in tiers:
        out += struct.pack("<QQ", thr & (2**64 - 1), thr >> 64) + struct.pack("<3Q", *bps)
    out += struct.pack("<I", 0)
    return bytes(out + b"\x00" * (4097 - len(out)))


FC = fee_config_bytes()
FC_SHA = hashlib.sha256(FC).hexdigest()
MON_PINS_F = copy.deepcopy(MON_PINS)
MON_PINS_F["accounts"]["pumpswap_fee_config"] = {"address": FEECFG, "sha256": FC_SHA}


def fee_pins(**over):
    p = dict(SHIPPED, fee_config_sha256=FC_SHA)
    p.update(over)
    return p


MINTS = [mon.b58encode(bytes([70 + i]) * 32) for i in range(8)]
CANON = [c.canonical_pool(m) for m in MINTS]
OTHER_POOL = mon.b58encode(bytes([7]) * 32)  # a WSOL pool that is not the canonical pool of any listed mint


def u64(x):
    return struct.pack("<Q", x)


def fee_ev(kind="BuyEvent", *, pool=None, user=None, q=80 * 10**9, v=17_580_000_000, b=200 * 10**12, sup=10**15, bps=(20, 5, 95), cashback=0, can_boost=True):
    pool = pool or CANON[0]
    over = {"pool": mon.b58decode(pool), "pool_quote_token_reserves": u64(q), "virtual_quote_reserves": v.to_bytes(16, "little", signed=True),
            "pool_base_token_reserves": u64(b), "base_supply": u64(sup), "lp_fee_basis_points": u64(bps[0]), "protocol_fee_basis_points": u64(bps[1]),
            "coin_creator_fee_basis_points": u64(bps[2]), "cashback_fee_basis_points": u64(cashback), "can_boost": b"\x01" if can_boost else b"\x00"}
    if user is not None:
        over["user"] = mon.b58decode(user)
    return encode(kind, prog="pump_amm", over=over), td.PUMPSWAP_PROGRAM


def good_fee_blobs():
    """tier 1 buys/sells (mcap 487.9 SOL), a tier 0 buy (mcap 225 SOL), a cashback coin, a zero-fee BOOST keeper slice."""
    return [fee_ev(), fee_ev("SellEvent", pool=CANON[1]), fee_ev(pool=CANON[2], q=50 * 10**9, v=17_500_000_000, b=300 * 10**12, bps=(2, 93, 30)),
            fee_ev(pool=CANON[3], bps=(20, 5, 0), cashback=95), fee_ev(pool=CANON[4], user=mon.boost_vault_authority(CANON[4]), bps=(0, 0, 0))]


class FeesClient(FakeClient):
    """FakeClient plus: the FeeConfig account, programdata headers, the upgrade authority's signature list, Pool accounts with base mints, unreadable pools."""

    def __init__(self, blobs, *, fee_config=FC, unreadable=(), header=None, **kw):
        super().__init__(blobs, **kw)
        self.fee_config, self.unreadable = fee_config, set(unreadable)
        self.pool_mints = dict(zip(CANON, MINTS))
        self.headers = header or {}
        self.auth_sigs: list[dict] = []
        self.set_writes({"PD_FEES": [(1100, FEES_DEPLOY)]}, pinned={"PD_FEES": 300})
        self.methods: list[str] = []

    def call(self, method, params):
        self.methods.append(method)
        if method == "getAccountInfo":
            self.calls += 1
            if params[0] == FEECFG:
                return {"value": None if self.fee_config is None else {"data": [base64.b64encode(self.fee_config).decode(), "base64"]}}
            if params[0] in self.headers:
                return {"value": {"data": [base64.b64encode(self.headers[params[0]]).decode(), "base64"]}}
            return {"value": None}
        if method == "getSignaturesForAddress" and params[0] == AUTH:
            self.calls += 1
            return self.auth_sigs
        if method == "getMultipleAccounts" and "dataSlice" not in params[1]:
            res = super().call(method, params)
            vals = []
            for key, val in zip(params[0], res["value"]):
                if key in self.unreadable:
                    vals.append(None)
                    continue
                data = bytearray(base64.b64decode(val["data"][0]))
                if key in self.pool_mints:
                    data[43:75] = mon.b58decode(self.pool_mints[key])
                vals.append({"data": [base64.b64encode(bytes(data)).decode(), "base64"]})
            return {"value": vals}
        return super().call(method, params)


def fees_rec(run_utc=FEES_RUN, **over):
    rec = make_rec(redeploy=False, run_utc=run_utc)
    rec["accounts"]["pumpswap_fee_config"]["sha256"] = FC_SHA
    rec["programs"]["fees"] = {"deploy_slot": 1100, "deploy_utc": FEES_DEPLOY, "programdata": "PD_FEES"}
    rec["halt"]["flags"]["pins_changed"] = flag(True, reason="program_fees deploy_slot 300 -> 1100")
    rec.update(over)
    return rec


def fees_decide(rec, tmp_path, *, client=None, pins=None, fp=None, now=None, state=None, history=(), fees_check=True, bounded=False, auth=None):
    return c.decide(rec, None, MON_PINS_F, pins or build_pins(), state or tmp_path / "state.jsonl", client=client or FeesClient(sample_blobs(good_fee_blobs())),
                    http_get=http_ok(), now_ms=now or (lambda: NOW), job_id="job-f", history=history, fees_check=fees_check,
                    fee_pins=fee_pins() if fp is None else fp, bounded_enum=bounded, upgrade_auth=auth)


# ---------------------------------------------------------------- gating: off by default, the HALT is unchanged
def test_fees_only_redeploy_without_the_flag_is_the_unchanged_hard_halt(tmp_path):
    boom = FeesClient([], fail_on={"getBlock", "getMultipleAccounts", "getSignaturesForAddress", "getTransaction"})
    v0 = c.decide(fees_rec(), None, MON_PINS_F, build_pins(), tmp_path / "a.jsonl", client=boom, http_get=http_ok(), now_ms=lambda: NOW, job_id="job-f")
    v1 = fees_decide(fees_rec(), tmp_path, client=boom, fees_check=False, state=tmp_path / "b.jsonl")
    assert v0 == v1 and "fees_check" not in v0
    assert v0["verdict"] == "HALT" and v0["halt_at_ms"] == ms(FEES_RUN) and any("fees" in r for r in v0["reasons"])
    assert not (tmp_path / "a.jsonl").exists() and not (tmp_path / "b.jsonl").exists() and boom.calls == 0


def test_with_the_flag_the_main_verdict_is_byte_identical_and_only_a_sub_verdict_is_added(tmp_path):
    off = fees_decide(fees_rec(), tmp_path, fees_check=False, state=tmp_path / "off.jsonl")
    on = fees_decide(fees_rec(), tmp_path, state=tmp_path / "on.jsonl")
    sub = on.pop("fees_check")
    assert json.dumps(on, sort_keys=True) == json.dumps(off, sort_keys=True)
    assert sub["verdict"] == "PASS", sub["reasons"]


@pytest.mark.parametrize("variant", ["pump_redeploy", "fees_and_pump", "boost_flag", "account_hash", "fees_unflagged"])
def test_flag_adds_nothing_when_the_fees_slot_is_not_the_only_change(variant, tmp_path):
    rec = fees_rec()
    if variant == "pump_redeploy":
        rec = make_rec()
        rec["accounts"]["pumpswap_fee_config"]["sha256"] = FC_SHA
    elif variant == "fees_and_pump":
        rec["programs"]["pump"] = {"deploy_slot": 1000, "deploy_utc": DEPLOY_PUMP, "programdata": "PD_PUMP"}
    elif variant == "boost_flag":
        rec["halt"]["flags"]["boost_disabled"] = flag(True)
    elif variant == "account_hash":
        rec["accounts"]["bonding_fee_config"]["sha256"] = "f" * 64
    elif variant == "fees_unflagged":
        rec["halt"]["flags"]["pins_changed"] = flag(False)  # a degraded record: two hard reasons
    cl_off, cl_on = FeesClient(sample_blobs()), FeesClient(sample_blobs())
    for cl in (cl_off, cl_on):
        cl.set_writes_from_rec(rec)
    off = fees_decide(copy.deepcopy(rec), tmp_path, client=cl_off, fees_check=False, state=tmp_path / "off.jsonl")
    on = fees_decide(copy.deepcopy(rec), tmp_path, client=cl_on, state=tmp_path / "on.jsonl")
    assert "fees_check" not in on and json.dumps(on, sort_keys=True) == json.dumps(off, sort_keys=True)
    for p in ("off.jsonl", "on.jsonl"):
        assert not (tmp_path / p).exists() or "fees:" not in (tmp_path / p).read_text()


# ---------------------------------------------------------------- PASS
def test_pass_is_record_only_with_counts_and_state(tmp_path):
    v = fees_decide(fees_rec(), tmp_path)
    f = v["fees_check"]
    assert f["verdict"] == "PASS" and f["record_only"] and f["halt_at_ms"] is None, f["reasons"]
    assert f["redeploys"] == [{"name": "fees", "slot": 1100, "deploy_utc": FEES_DEPLOY}] and f["per_write"] == {"fees:1100": "PASS"}
    ft = f["detail"]["fee_tiers"]
    assert ft["n_tested"] >= SHIPPED["min_universe_events"] and ft["n_mismatch"] == 0 and ft["tiers_covered"] == [0, 1]
    assert ft["n_boost_slices"] > 0 and ft["n_boost_slices_with_fee"] == 0 and ft["n_outside_non_canonical"] > 0  # the fixture's default Buy/Sell sit on a non-canonical pool
    assert f["detail"]["sample_start_slot"] == 1100 + c.SAMPLE_START_OFFSET_SLOTS and f["deadline_utc"] == "2026-10-21T01:00:00Z"
    st = c.read_state(tmp_path / "state.jsonl")
    assert st["fees:1100"]["verdict"] == "PASS" and st["fees:1100"]["program"] == "fees" and st["fees:1100"]["job_id"] == "job-f"


def test_passed_fees_write_is_covered_on_the_next_run(tmp_path):
    fees_decide(fees_rec(), tmp_path)
    boom = FeesClient([], fail_on={"getBlock", "getMultipleAccounts", "getAccountInfo"})
    v = fees_decide(fees_rec(run_utc="2026-10-20T19:11:27Z"), tmp_path, client=boom, now=lambda: ms("2026-10-20T19:15:00Z"))
    assert v["fees_check"]["verdict"] == "PASS" and v["fees_check"].get("covered") and v["verdict"] == "HALT"


def test_cashback_coin_moves_the_creator_leg_and_still_matches():
    ev, _bad = c.fee_events([fee_ev(bps=(20, 5, 0), cashback=95)[0], fee_ev(bps=(20, 5, 40), cashback=55)[0]], c_idls())
    out = c.classify_fee_events(ev, {CANON[0]: {"base_mint": MINTS[0], "quote_mint": mon.WSOL_MINT}}, TIERS)
    assert out["n_tested"] == 2 and out["n_match"] == 2


def c_idls():
    from tools.test_a3_redeploy_check import IDLS
    return IDLS


# ---------------------------------------------------------------- FAIL
def run_fail(tmp_path, extra, **kw):
    cl = FeesClient(sample_blobs(good_fee_blobs() + extra), **kw)
    return fees_decide(fees_rec(), tmp_path, client=cl)["fees_check"]


def test_wrong_tier_bps_fails_at_the_fees_write_instant(tmp_path):
    f = run_fail(tmp_path, [fee_ev(pool=CANON[5], bps=(2, 93, 30))])  # tier-0 bps at a tier-1 market cap
    assert f["verdict"] == "FAIL" and f["halt_at_ms"] == ms(FEES_DEPLOY)
    assert any(r.startswith("fees:1100: i:") and "tier 1 expected [20, 5, 95] observed [2, 93, 30]" in r for r in f["reasons"])
    assert c.read_state(tmp_path / "state.jsonl")["fees:1100"]["verdict"] == "FAIL"


def test_boost_slice_with_a_fee_fails(tmp_path):
    f = run_fail(tmp_path, [fee_ev(pool=CANON[5], user=mon.boost_vault_authority(CANON[5]), bps=(20, 5, 95))])
    assert f["verdict"] == "FAIL" and any("BOOST keeper slices" in r for r in f["reasons"])


def test_unpriceable_in_universe_event_is_a_mismatch(tmp_path):
    f = run_fail(tmp_path, [fee_ev(pool=CANON[5], q=1, v=-5)])
    assert f["verdict"] == "FAIL" and any("unpriceable" in r and "unpriceable 0;" not in r for r in f["reasons"])


def test_unreadable_pool_counts_in_the_universe(tmp_path):
    f = run_fail(tmp_path, [fee_ev(pool=OTHER_POOL, bps=(25, 5, 0))], unreadable={OTHER_POOL})
    assert f["verdict"] == "FAIL" and f["halt_at_ms"] == ms(FEES_DEPLOY)


def test_outside_events_are_counted_not_tested(tmp_path):
    extra = [fee_ev(pool=OTHER_POOL, bps=(25, 5, 0)),  # non-canonical WSOL pool (flat fees)
             fee_ev(pool=CANON[5], bps=(0, 0, 0), can_boost=False)]  # not a BOOST pool
    v = fees_decide(fees_rec(), tmp_path, client=FeesClient(sample_blobs(good_fee_blobs() + extra)))
    ft = v["fees_check"]["detail"]["fee_tiers"]
    assert v["fees_check"]["verdict"] == "PASS" and ft["n_outside_non_canonical"] > 0 and ft["n_outside_no_boost"] > 0
    nonwsol = FeesClient(sample_blobs(good_fee_blobs() + [fee_ev(pool=CANON[6], bps=(1, 1, 1))]))
    nonwsol.pool_quote = bytes([9]) * 32  # every pool quotes another mint: nothing is in the universe
    with pytest.raises(c.CheckError, match="in-universe WSOL trade events tested"):
        ev, _ = c.fee_events([fee_ev(pool=CANON[6], bps=(1, 1, 1))[0]], c_idls())
        c.fee_tier_failures(dict(c.classify_fee_events(ev, {CANON[6]: {"base_mint": MINTS[6], "quote_mint": "X"}}, TIERS), n_idl_undecodable=0), fee_pins())


def test_a_changed_pump_fees_idl_fails(tmp_path):
    body = json.dumps({"metadata": {"name": "pump_fees"}, "events": [], "types": [], "x": 1}).encode()
    v = c.decide(fees_rec(), None, MON_PINS_F, build_pins(), tmp_path / "s.jsonl", client=FeesClient(sample_blobs(good_fee_blobs())), http_get=http_ok({"pump_fees": body}),
                 now_ms=lambda: NOW, fees_check=True, fee_pins=fee_pins())
    assert v["fees_check"]["verdict"] == "FAIL" and any("f: idl pump_fees" in r for r in v["fees_check"]["reasons"])


def test_unevaluated_boost_flag_fails_check_a(tmp_path):
    rec = fees_rec()
    rec["halt"]["flags"]["boost_share_low"] = flag(False, evaluated=False)  # one run only: not a hard reason, so still a fees-only trigger
    f = fees_decide(rec, tmp_path)["fees_check"]
    assert f["verdict"] == "FAIL" and any("a: boost_share_low not evaluated" in r for r in f["reasons"])


def test_fee_config_changed_at_check_time_fails(tmp_path):
    other = bytearray(FC)
    other[-1] = 1
    f = run_fail(tmp_path, [], fee_config=bytes(other))
    assert f["verdict"] == "FAIL" and any("pumpswap_fee_config read at check time differs" in r for r in f["reasons"])


def test_timeout_fails(tmp_path):
    f = fees_decide(fees_rec(), tmp_path, now=lambda: ms("2026-10-21T01:00:01Z"))["fees_check"]
    assert f["verdict"] == "FAIL" and "timeout" in f["reasons"][0] and f["halt_at_ms"] == ms(FEES_DEPLOY)


def test_fail_is_sticky(tmp_path):
    run_fail(tmp_path, [fee_ev(pool=CANON[5], bps=(2, 93, 30))])
    v = fees_decide(fees_rec(run_utc="2026-10-20T19:11:27Z"), tmp_path, now=lambda: ms("2026-10-20T19:15:00Z"))
    assert v["fees_check"]["verdict"] == "FAIL" and v["fees_check"]["halt_at_ms"] == ms(FEES_DEPLOY) and "state: fees:1100 was FAIL" in v["fees_check"]["reasons"][0]


# ---------------------------------------------------------------- ERROR
def test_too_few_in_universe_events_is_error(tmp_path):
    f = fees_decide(fees_rec(), tmp_path, client=FeesClient(sample_blobs()))["fees_check"]
    assert f["verdict"] == "ERROR" and "in-universe WSOL trade events tested" in f["reasons"][0] and f["halt_at_ms"] == ms(FEES_DEPLOY)


def test_enumeration_that_does_not_reach_the_pinned_slot_is_error_at_the_bound(tmp_path):
    cl = FeesClient(sample_blobs(good_fee_blobs()))
    cl.write_sigs["PD_FEES"] = [x for x in cl.write_sigs["PD_FEES"] if not x["signature"].startswith("pinned")]
    f = fees_decide(fees_rec(), tmp_path, client=cl)["fees_check"]
    assert f["verdict"] == "ERROR" and "not reached" in f["reasons"][0]
    assert f["halt_at_ms"] == ms(MON_PINS_F["programs"]["fees"]["deploy_utc"])  # no earlier record at the pinned slot: the pinned deploy instant
    assert c.read_state(tmp_path / "state.jsonl")["fees:1100"]["verdict"] == "ERROR"
    hist = [fees_rec(run_utc="2026-10-19T19:11:27Z")]
    hist[0]["programs"]["fees"]["deploy_slot"] = 300
    f2 = fees_decide(fees_rec(), tmp_path / "h", client=cl, history=hist, state=tmp_path / "h.jsonl")["fees_check"]
    assert f2["verdict"] == "ERROR" and f2["halt_at_ms"] == ms("2026-10-19T19:11:27Z")


def test_tier_table_that_differs_from_the_account_is_error(tmp_path):
    bad = [list(t) for t in TIERS]
    bad[1] = [bad[1][0], [20, 5, 94]]
    f = fees_decide(fees_rec(), tmp_path, fp=fee_pins(canonical_tiers=bad))["fees_check"]
    assert f["verdict"] == "ERROR" and "pin inconsistent" in f["reasons"][0]


@pytest.mark.parametrize("over", [{"canonical_tiers": None}, {"canonical_tiers": [[5, [1, 2, 3]]]}, {"canonical_tiers": [[0, [1, 2]]]},
                                  {"canonical_tiers": [[0, [1, 2, 3]], [0, [1, 2, 3]]]}, {"fee_config_sha256": "e" * 64}, {"min_universe_events": 0},
                                  {"max_mismatch_share": 0.2}, {"max_mismatch_share": True}, {"fee_config_account": "bonding_fee_config"}])
def test_bad_fee_pins_are_error(over, tmp_path):
    with pytest.raises(c.CheckError):
        c.validate_fee_pins(fee_pins(**over), MON_PINS_F)
    f = fees_decide(fees_rec(), tmp_path, fp=fee_pins(**over))["fees_check"]
    assert f["verdict"] == "ERROR"


def test_missing_fee_pins_file_is_error(tmp_path):
    v = c.decide(fees_rec(), None, MON_PINS_F, build_pins(), tmp_path / "s.jsonl", client=FeesClient(sample_blobs(good_fee_blobs())), http_get=http_ok(),
                 now_ms=lambda: NOW, fees_check=True, fee_pins=None)
    assert v["fees_check"]["verdict"] == "ERROR" and "pin missing" in v["fees_check"]["reasons"][0]


def test_state_write_failure_is_a_sub_verdict_error_and_the_main_halt_stands(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(c, "append_state", boom)
    v = fees_decide(fees_rec(), tmp_path)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ms(FEES_RUN)
    assert v["fees_check"]["verdict"] == "ERROR" and v["fees_check"]["halt_at_ms"] == ms(FEES_RUN)


# ---------------------------------------------------------------- shipped pins and pure parts
def test_shipped_pins_validate_against_the_shipped_monitor_pins_and_match_the_lab_fee_table():
    mp = json.loads((c.REPO_ROOT / "tools" / "pump_structure_pins.json").read_text())
    fp = c.validate_fee_pins(SHIPPED, mp)
    assert fp["max_mismatch_share"] == 0.0 and fp["min_universe_events"] == 100
    from tools import paper_curve_math as pcm
    want = [[int(round(m * 10**9)), [lp // 100, pr // 100, cr // 100]] for m, cr, pr, lp in pcm.PUMPSWAP_SOL_FEE_SPLIT]
    # the docs table (20 May 2026) and the account agree on thresholds and the lp / protocol legs; the creator leg differs only where the docs give half-bp values
    assert [t[0] for t in TIERS] == [t[0] for t in want] and [t[1][:2] for t in TIERS] == [t[1][:2] for t in want]
    assert all(abs(a[1][2] - b[1][2]) <= 1 for a, b in zip(TIERS, want))
    assert set(SHIPPED["upgrade_authority"]) == {"pump", "pumpswap", "fees"}


def test_decode_fee_tiers_round_trip_and_short_account():
    assert c.decode_fee_tiers(FC) == TIERS
    with pytest.raises(c.CheckError):
        c.decode_fee_tiers(FC[:100])


def test_tier_index_boundaries():
    assert c.fee_tier_index(TIERS, 0) == 0 and c.fee_tier_index(TIERS, 419_999_999_999) == 0
    assert c.fee_tier_index(TIERS, 420_000_000_000) == 1 and c.fee_tier_index(TIERS, 10**20) == len(TIERS) - 1


def test_canonical_pool_matches_the_executor_derivation():
    tx = pytest.importorskip("tools.pumpswap_tx")
    from solders.pubkey import Pubkey
    for m in MINTS[:3]:
        assert str(tx.canonical_pool(Pubkey.from_string(m))) == c.canonical_pool(m)


def test_load_fees_books(tmp_path):
    p = tmp_path / "b.json"
    assert c.load_fees_books(None) == [] and c.load_fees_books(str(tmp_path / "missing.json")) == []
    p.write_text(json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": ["x"]}))
    assert c.load_fees_books(str(p)) == []
    p.write_text(json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": ["x"], "fees_check_bot_ids": ["x"]}))
    assert c.load_fees_books(str(p)) == ["x"]
    p.write_text(json.dumps({"fees_check_bot_ids": "x"}))
    assert c.load_fees_books(str(p)) == []
    p.write_text("{not json")
    assert c.load_fees_books(str(p)) == []


# ---------------------------------------------------------------- main(): --books gates it
def _main_files(tmp_path, books):
    (tmp_path / "daily.jsonl").write_text(json.dumps(fees_rec()) + "\n")
    (tmp_path / "mp.json").write_text(json.dumps(MON_PINS_F))
    (tmp_path / "ap.json").write_text(json.dumps(build_pins()))
    (tmp_path / "fp.json").write_text(json.dumps(fee_pins()))
    if books is not None:
        (tmp_path / "books.json").write_text(json.dumps(books))
    a = ["--daily", str(tmp_path / "daily.jsonl"), "--monitor-pins", str(tmp_path / "mp.json"), "--pins", str(tmp_path / "ap.json"), "--state", str(tmp_path / "st.jsonl"),
         "--verdict-out", str(tmp_path / "v.json"), "--fees-pins", str(tmp_path / "fp.json")]
    return a + (["--books", str(tmp_path / "books.json")] if books is not None else [])


def _header(slot, auth=AUTH, opt=1):
    return struct.pack("<I", 3) + struct.pack("<Q", slot) + bytes([opt]) + mon.b58decode(auth)


def test_main_without_books_is_unchanged_and_with_the_flag_adds_the_sub_verdict(tmp_path, capsys):
    for sub, books in (("none", None), ("noflag", {"legacy_bot_ids": [], "new_rule_bot_ids": ["h5x"]})):
        d = tmp_path / sub
        d.mkdir()
        assert c.main(_main_files(d, books), client=FeesClient([], fail_on={"getBlock", "getMultipleAccounts", "getAccountInfo", "getSignaturesForAddress"}),
                      http_get=http_ok(), now_ms=lambda: NOW) == 10
        assert "fees_check" not in json.loads((d / "v.json").read_text())
    assert (tmp_path / "none" / "v.json").read_text() == (tmp_path / "noflag" / "v.json").read_text()
    d = tmp_path / "flag"
    d.mkdir()
    cl = FeesClient(sample_blobs(good_fee_blobs()), header={"PD_FEES": _header(1100)})
    cl.auth_sigs = [{"signature": "wPD_FEES1100", "slot": 1100, "blockTime": ms(FEES_DEPLOY) // 1000, "err": None},
                    {"signature": "old", "slot": 200, "blockTime": 1, "err": None}]
    cl.write_txs["wPD_FEES1100"]["transaction"]["message"]["instructions"][0]["accounts"] = [1]
    assert c.main(_main_files(d, {"legacy_bot_ids": [], "new_rule_bot_ids": ["h5x"], "fees_check_bot_ids": ["h5x"]}), client=cl, http_get=http_ok(), now_ms=lambda: NOW) == 10
    v = json.loads((d / "v.json").read_text())
    assert v["verdict"] == "HALT" and v["fees_check"]["verdict"] == "PASS", v["fees_check"]["reasons"]
    assert "getAccountInfo" in cl.methods  # bounded: the programdata header was read
    v.pop("fees_check")
    assert json.dumps(v, sort_keys=True) == json.dumps(json.loads((tmp_path / "none" / "v.json").read_text()), sort_keys=True)


# ---------------------------------------------------------------- bounded upgrade-write enumeration (job #1545)
PD = "PDATA111111111111111111111111111111111111111"
PD_OTHER = "PDOTHER11111111111111111111111111111111111"


class AuthClient:
    """Programdata header + the authority's signature list + its txs. Any call to the programdata's own signature list is an error (that is the walk this replaces)."""

    def __init__(self, cur_slot, *, auth=AUTH, opt=1, txs=(), reach=True):
        self.header = _header(cur_slot, auth, opt)
        self.txs = {}
        self.sigs = []
        for sig, slot, ixs, err in txs:
            self.txs[sig] = {"slot": slot, "blockTime": 1_792_000_000 + slot, "transaction": {"message": {"accountKeys": ["Signer", PD, PD_OTHER, c.LOADER_PROGRAM, AUTH],
                             "instructions": []}}, "meta": {"err": err, "innerInstructions": [{"index": 0, "instructions": ixs}]}}
            self.sigs.append({"signature": sig, "slot": slot, "blockTime": 1_792_000_000 + slot, "err": err})
        if reach:
            self.sigs.append({"signature": "before-pin", "slot": 90, "blockTime": 1, "err": None})
        self.sigs.sort(key=lambda s: -s["slot"])
        self.methods = []

    def call(self, method, params):
        self.methods.append((method, params[0]))
        if method == "getAccountInfo" and params[0] == PD:
            return {"value": {"data": [base64.b64encode(self.header).decode(), "base64"]}}
        if method == "getSignaturesForAddress" and params[0] == AUTH:
            return self.sigs
        if method == "getTransaction":
            return self.txs.get(params[0])
        raise AssertionError((method, params[0]))


def loader_ix(tag, target=1):
    return {"programIdIndex": 3, "accounts": [target, 4], "data": mon.b58encode(struct.pack("<I", tag))}


def test_bounded_unchanged_slot_is_zero_writes_in_one_call():
    cl = AuthClient(100)
    assert c.list_upgrade_writes_bounded(cl, "pumpswap", PD, 100, 100, AUTH) == []
    assert cl.methods == [("getAccountInfo", PD)]


def test_bounded_one_upgrade():
    cl = AuthClient(500, txs=[("u500", 500, [loader_ix(3)], None), ("other", 450, [loader_ix(3, target=2)], None), ("noise", 300, [], None)])
    ws = c.list_upgrade_writes_bounded(cl, "pumpswap", PD, 100, 500, AUTH)
    assert [w["new"] for w in ws] == [500] and ws[0]["sig"] == "u500"
    assert ("getSignaturesForAddress", PD) not in cl.methods


def test_bounded_two_upgrades_and_failed_tx_ignored():
    cl = AuthClient(500, txs=[("u300", 300, [loader_ix(3)], None), ("u400f", 400, [loader_ix(3)], {"err": 1}), ("u500", 500, [loader_ix(3)], None)])
    ws = c.list_upgrade_writes_bounded(cl, "pump", PD, 100, 500, AUTH)
    assert [w["new"] for w in ws] == [300, 500] and all(w["old"] == 100 for w in ws)


@pytest.mark.parametrize("case", ["header_authority", "no_authority", "set_authority_in_walk", "set_authority_checked_in_walk"])
def test_bounded_authority_change_fails_closed(case):
    if case == "header_authority":
        cl = AuthClient(500, auth="NewAuth111111111111111111111111111111111111", txs=[("u500", 500, [loader_ix(3)], None)])
    elif case == "no_authority":
        cl = AuthClient(500, opt=0, txs=[("u500", 500, [loader_ix(3)], None)])
    else:
        tag = 4 if case == "set_authority_in_walk" else 7
        cl = AuthClient(500, txs=[("sa", 200, [loader_ix(tag)], None), ("u500", 500, [loader_ix(3)], None)])
    with pytest.raises(c.CheckError, match="authority"):
        c.list_upgrade_writes_bounded(cl, "pumpswap", PD, 100, 500, AUTH)


@pytest.mark.parametrize("case", ["not_reached", "slot_disagrees", "newest_missing", "no_pin", "unreadable_tx"])
def test_bounded_other_failures_are_errors(case):
    args = (100, 500)
    if case == "not_reached":
        cl = AuthClient(500, txs=[("u500", 500, [loader_ix(3)], None)], reach=False)
    elif case == "slot_disagrees":
        cl, args = AuthClient(600, txs=[("u500", 500, [loader_ix(3)], None)]), (100, 500)
    elif case == "newest_missing":
        cl = AuthClient(500, txs=[("u300", 300, [loader_ix(3)], None)])
    elif case == "unreadable_tx":
        cl = AuthClient(500, txs=[("u500", 500, [loader_ix(3)], None)])
        cl.txs.clear()
    else:
        cl = AuthClient(500)
    with pytest.raises(c.CheckError):
        c.list_upgrade_writes_bounded(cl, "pumpswap", PD, *args, None if case == "no_pin" else AUTH)


def test_decide_uses_the_bounded_walk_only_with_the_flag(tmp_path):
    """A pump redeploy: without the flag the programdata list is walked (as merged); with bounded_enum the header + authority list are used and the verdict is the same."""
    rec = make_rec()
    rec["accounts"]["pumpswap_fee_config"]["sha256"] = FC_SHA
    cl0 = FeesClient(sample_blobs())
    cl0.set_writes_from_rec(rec)
    v0 = fees_decide(copy.deepcopy(rec), tmp_path, client=cl0, fees_check=False, state=tmp_path / "a.jsonl")
    assert "getAccountInfo" not in cl0.methods and v0["verdict"] == "PASS"
    cl1 = FeesClient(sample_blobs(), header={"PD_PUMP": _header(1000), "PD_SWAP": _header(1100)})
    cl1.write_sigs.pop("PD_PUMP")
    cl1.write_sigs.pop("PD_SWAP")  # the programdata lists are gone: only the bounded route can answer
    cl1.auth_sigs = [{"signature": "wPD_SWAP1100", "slot": 1100, "blockTime": ms("2026-10-19T17:47:20Z") // 1000, "err": None},
                     {"signature": "wPD_PUMP1000", "slot": 1000, "blockTime": ms(DEPLOY_PUMP) // 1000, "err": None},
                     {"signature": "old", "slot": 50, "blockTime": 1, "err": None}]
    for sig in ("wPD_PUMP1000", "wPD_SWAP1100"):
        cl1.write_txs[sig]["transaction"]["message"]["instructions"][0]["accounts"] = [1]
    v1 = fees_decide(copy.deepcopy(rec), tmp_path, client=cl1, fees_check=False, bounded=True, auth=SHIPPED["upgrade_authority"], state=tmp_path / "b.jsonl")
    assert v1["verdict"] == "PASS" and v1["redeploys"] == v0["redeploys"]
    assert "getAccountInfo" in cl1.methods
