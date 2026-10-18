"""Tests for tools/hv4_chain.py, second file: recorded public account bytes, the RPC layer, mint_facts, largest_holders, the Mayhem flag,
the token bucket, the credit counter, key hygiene. No network. Builders for spec-built account bytes live in tools/test_hv4_chain.py.

Recorded bytes: tools/fixtures/hv4_chain/recorded.json (public mainnet mint and PumpSwap Pool accounts, one holder list; see its `_provenance`).
"""
from __future__ import annotations

import ast
import base64
import json
import logging
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from tools import hv4_chain as H
from tools import pump_structure_monitor as M
from tools.test_hv4_chain import (  # builders written from the spl-token-2022 layout
    FX_PATH, MINT, POOL, K, build_base, build_t22, v_hook, v_metadata_pointer, v_transfer_fee,
)

REC = json.loads(FX_PATH.read_text())
REC_MINTS = REC["mints"]
REC_POOLS = REC["pools"]


def rec_bytes(entry: dict) -> bytes:
    return base64.b64decode(entry["data_b64"])


# ======================================================================================================================
# recorded public account bytes
# ======================================================================================================================
@pytest.mark.parametrize("mint", sorted(REC_MINTS))
def test_recorded_mints_match_the_nodes_own_reading(mint):
    e = REC_MINTS[mint]
    p = H.parse_mint_account(rec_bytes(e), e["owner"])
    node = e["node_reading"]
    assert p.mint_authority == node["mintAuthority"] and p.freeze_authority == node["freezeAuthority"]
    assert str(p.supply_raw) == node["supply"] and p.decimals == node["decimals"]
    assert sorted(p.extensions) == node["extensions"]


def test_recorded_pump_mints_are_token_2022_with_only_metadata_extensions_and_clean():
    n = 0
    for e in REC_MINTS.values():
        if e["label"].startswith(("non_synthetic", "synthetic")):
            p = H.parse_mint_account(rec_bytes(e), e["owner"])
            assert p.token_program == "token-2022" and p.extensions == ("metadataPointer", "tokenMetadata") and p.veto_reasons == ()
            n += 1
    assert n == 6


def test_recorded_pyusd_and_usdg_carry_the_named_risk_extensions():
    for label in ("PYUSD", "USDG"):
        e = next(v for v in REC_MINTS.values() if v["label"] == label)
        p = H.parse_mint_account(rec_bytes(e), e["owner"])
        assert {"transferHook", "permanentDelegate", "transferFeeConfig", "confidentialTransferMint"} <= set(p.extensions)
        assert {H.V_HOOK, H.V_DELEGATE, H.V_MINT_AUTH, H.V_FREEZE_AUTH} <= set(p.veto_reasons)
        # on chain these hooks carry a null program id (inert today); the extension is still vetoed, because DESIGN lists the extension
        # and the hook authority can set a program later
        assert p.ext_info["transferHook"]["program_id"] is None and H.V_HOOK in p.veto_reasons


def test_recorded_classic_spl_mints():
    wsol = next(v for v in REC_MINTS.values() if v["label"] == "WSOL")
    usdc = next(v for v in REC_MINTS.values() if v["label"] == "USDC")
    assert H.parse_mint_account(rec_bytes(wsol), wsol["owner"]).veto_reasons == ()
    assert H.parse_mint_account(rec_bytes(usdc), usdc["owner"]).veto_reasons == (H.V_MINT_AUTH, H.V_FREEZE_AUTH)


@pytest.mark.parametrize("pool", sorted(REC_POOLS))
def test_recorded_pool_layout_and_derived_addresses(pool):
    e = REC_POOLS[pool]
    data = rec_bytes(e)
    info = H.parse_pool_account(data, e["owner"])
    assert len(data) == 301
    tp = REC_MINTS[e["mint"]]["owner"]
    assert info.base_mint == e["mint"] and info.quote_mint == H.WSOL_MINT
    assert info.base_vault == H.associated_token_address(pool, tp, e["mint"])  # the vault is the pool's ATA: offsets and ATA seeds agree
    assert info.quote_vault == H.associated_token_address(pool, H.SPL_TOKEN_PROGRAM, H.WSOL_MINT)
    assert H.bonding_curve_pda(e["mint"]) == e["curve"]
    assert info.is_mayhem is False


def test_pool_discriminator_is_the_anchor_one():
    assert H.POOL_DISCRIMINATOR.hex() == "f19a6d0411b16dbc"
    assert all(rec_bytes(e)[:8] == H.POOL_DISCRIMINATOR for e in REC_POOLS.values())


def test_pool_parse_fails_closed():
    good = rec_bytes(next(iter(REC_POOLS.values())))
    with pytest.raises(H.ChainParseError):
        H.parse_pool_account(good, H.SPL_TOKEN_PROGRAM)  # wrong owner
    with pytest.raises(H.ChainParseError):
        H.parse_pool_account(good[:243], H.PUMPSWAP_PROGRAM)  # short
    bad = bytearray(good)
    bad[0] ^= 0xFF
    with pytest.raises(H.ChainParseError):
        H.parse_pool_account(bytes(bad), H.PUMPSWAP_PROGRAM)  # discriminator
    bad = bytearray(good)
    bad[H.POOL_MAYHEM] = 2
    with pytest.raises(H.ChainParseError):
        H.parse_pool_account(bytes(bad), H.PUMPSWAP_PROGRAM)  # flag byte not 0 or 1
    bad[H.POOL_MAYHEM] = 1
    assert H.parse_pool_account(bytes(bad), H.PUMPSWAP_PROGRAM).is_mayhem is True


# ======================================================================================================================
# fakes
# ======================================================================================================================
class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s

    def now_ms(self) -> int:
        return int(1_760_000_000_000 + self.t * 1000)


class FakeNode:
    """Stands in for the HTTP post. `script` maps method -> replies (a dict, or an Exception to raise), consumed in order; the last repeats."""

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, list]] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> dict:
        m = payload["method"]
        self.calls.append((m, payload["params"]))
        q = self.script[m]
        r = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(r, Exception):
            raise r
        return r

    def n(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)


def account_reply(data: bytes, owner: str, slot: int = 500) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": slot}, "value": {
        "data": [base64.b64encode(data).decode(), "base64"], "executable": False, "lamports": 1, "owner": owner, "rentEpoch": 0, "space": len(data)}}}


def rpc_error(code: int) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": "x"}}


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    hdrs = Message()
    if retry_after:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.invalid/?api-key=LEAKME12345", code, "msg", hdrs, None)


def make_chain(script: dict[str, list], clock: Clock | None = None, **kw):
    clock = clock or Clock()
    node = FakeNode(script)
    bucket = H.TokenBucket(1000.0, burst=1000.0, clock=clock.now, sleep=clock.sleep)
    rpc = H.RpcClient(H.RpcConfig.public(), bucket=bucket, post=node, sleep=clock.sleep, now_ms=clock.now_ms, backoff_s=1.0, **kw)
    chain = H.HV4Chain(rpc, H.TtlCache(clock=clock.now), now_ms=clock.now_ms)
    return chain, node, clock


# ======================================================================================================================
# mint_facts through the RPC layer
# ======================================================================================================================
def test_mint_facts_clean_pump_mint_and_card_fields():
    e = next(v for v in REC_MINTS.values() if v["label"] == "non_synthetic_1")
    chain, node, _ = make_chain({"getAccountInfo": [account_reply(rec_bytes(e), e["owner"], slot=777)]})
    f = chain.mint_facts(MINT)
    assert f.ok and not f.vetoed and f.allows_transfer and f.as_of_slot == 777 and f.as_of_ms
    assert f.token_program == "token-2022" and f.extensions == ("metadataPointer", "tokenMetadata")
    card = f.to_card_fields()
    assert card["mint_authority"] is None and card["freeze_authority"] is None and card["mint_veto_reasons"] == []
    assert node.calls[0][1][1] == {"encoding": "base64", "commitment": "confirmed"}


def test_mint_facts_vetoed_mint_reports_reasons_and_blocks_transfer():
    chain, _, _ = make_chain({"getAccountInfo": [account_reply(build_t22([(14, v_hook()), (1, v_transfer_fee(10))]), H.TOKEN_2022_PROGRAM)]})
    f = chain.mint_facts(MINT)
    assert f.ok and f.vetoed and not f.allows_transfer
    assert set(f.veto_reasons) == {H.V_HOOK, H.V_FEE}


def test_authority_only_veto_still_allows_transfer():
    chain, _, _ = make_chain({"getAccountInfo": [account_reply(build_base(mint_auth=K(1)), H.SPL_TOKEN_PROGRAM)]})
    f = chain.mint_facts(MINT)
    assert f.vetoed and f.allows_transfer and f.veto_reasons == (H.V_MINT_AUTH,)


@pytest.mark.parametrize("reply", [
    rpc_error(-32005),
    rpc_error(-32602),
    {"jsonrpc": "2.0", "id": 1},  # no result
    {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 5}, "value": None}},  # account not found
    {"jsonrpc": "2.0", "id": 1, "result": {"value": {"data": ["", "base64"], "owner": H.SPL_TOKEN_PROGRAM}}},  # no context slot
    {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": True}, "value": None}},  # bool is not a slot
    {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 5}, "value": {"data": "abc", "owner": H.SPL_TOKEN_PROGRAM}}},  # data shape
    {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 5}, "value": {"data": ["!!", "base64"], "owner": H.SPL_TOKEN_PROGRAM}}},
    {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 5}, "value": {"data": ["AAAA", "base58"], "owner": H.SPL_TOKEN_PROGRAM}}},
    ["not", "a", "dict"],
    account_reply(build_base(), "11111111111111111111111111111111"),  # not a token program
    account_reply(b"\0" * 10, H.SPL_TOKEN_PROGRAM),
])
def test_mint_facts_fails_closed_on_any_bad_answer(reply):
    chain, _, _ = make_chain({"getAccountInfo": [reply]}, attempts=1)
    f = chain.mint_facts(MINT)
    assert f.ok is False and f.vetoed and f.allows_transfer is False
    assert f.error and f.veto_reasons == (f"{H.CHAIN_ERROR}:{f.error}",)
    assert f.mint_authority is None and f.extensions == ()


def test_mint_facts_bad_address_makes_no_call():
    chain, node, _ = make_chain({"getAccountInfo": [account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]})
    for bad in ("", "not-a-key", "0OIl" * 9, None, 12):
        f = chain.mint_facts(bad)  # type: ignore[arg-type]
        assert f.ok is False and f.error == "bad_mint_address"
    assert node.calls == []


def test_mint_facts_unexpected_exception_fails_closed_without_leaking_text():
    chain, _, _ = make_chain({"getAccountInfo": [KeyError("https://example.invalid/?api-key=LEAKME12345")]}, attempts=1)
    f = chain.mint_facts(MINT)
    assert f.ok is False and "LEAKME" not in repr(f) and "example.invalid" not in repr(f)


def test_mint_facts_cache_clean_is_long_vetoed_is_short_errors_are_not_cached():
    chain, node, clock = make_chain({"getAccountInfo": [account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]})
    a = chain.mint_facts(MINT)
    b = chain.mint_facts(MINT)
    assert a is b and node.n("getAccountInfo") == 1 and chain.counter.cache_hits == 1
    clock.t += H.TTL_MINT_VETOED_S + 1
    assert chain.mint_facts(MINT) is a  # clean results outlive the vetoed TTL
    clock.t += H.TTL_MINT_CLEAN_S
    assert chain.mint_facts(MINT) is not a and node.n("getAccountInfo") == 2
    assert chain.mint_facts(MINT, force=True) is not a and node.n("getAccountInfo") == 3

    chain, node, clock = make_chain({"getAccountInfo": [account_reply(build_base(freeze_auth=K(2)), H.SPL_TOKEN_PROGRAM),
                                                        account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]})
    assert chain.mint_facts(MINT).vetoed
    assert chain.mint_facts(MINT).vetoed and node.n("getAccountInfo") == 1  # still cached
    clock.t += H.TTL_MINT_VETOED_S + 1
    assert not chain.mint_facts(MINT).vetoed and node.n("getAccountInfo") == 2  # freeze authority revoked meanwhile

    chain, node, _ = make_chain({"getAccountInfo": [rpc_error(-32602), account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]}, attempts=1)
    assert chain.mint_facts(MINT).ok is False
    assert chain.mint_facts(MINT).ok is True and node.n("getAccountInfo") == 2


# ---- RPC retry, cap, pacing ---------------------------------------------------------------------------------------------------
def test_rpc_retries_429_then_succeeds_and_counts_every_attempt():
    chain, node, clock = make_chain({"getAccountInfo": [http_error(429), http_error(503), account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]})
    assert chain.mint_facts(MINT).ok
    assert node.n("getAccountInfo") == 3 and chain.counter.calls == 3 and chain.counter.errors == 2
    assert clock.slept == [1.0, 2.0]  # exponential backoff


def test_rpc_honours_retry_after_and_gives_up_with_a_short_code():
    chain, node, clock = make_chain({"getAccountInfo": [http_error(429, "7")]})
    f = chain.mint_facts(MINT)
    assert f.ok is False and f.error == "http_429" and node.n("getAccountInfo") == 3
    assert 7.0 in clock.slept


def test_rpc_does_not_retry_a_hard_error():
    chain, node, _ = make_chain({"getAccountInfo": [rpc_error(-32602)]})
    assert chain.mint_facts(MINT).error == "rpc_-32602" and node.n("getAccountInfo") == 1
    chain, node, _ = make_chain({"getAccountInfo": [http_error(403)]})
    assert chain.mint_facts(MINT).error == "http_403" and node.n("getAccountInfo") == 1


def test_rpc_network_errors_retry_and_the_code_carries_no_text():
    chain, node, _ = make_chain({"getAccountInfo": [urllib.error.URLError("https://example.invalid/?api-key=LEAKME12345 refused")]})
    f = chain.mint_facts(MINT)
    assert f.error == "network_URLError" and node.n("getAccountInfo") == 3


def test_daily_call_cap_stops_calls_and_resets_on_the_next_utc_day():
    chain, node, clock = make_chain({"getAccountInfo": [account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]}, daily_call_cap=3)
    for _ in range(3):
        assert chain.mint_facts(MINT, force=True).ok
    f = chain.mint_facts(MINT, force=True)
    assert f.ok is False and f.error == "daily_call_cap" and node.n("getAccountInfo") == 3
    clock.t += 86400
    assert chain.mint_facts(MINT, force=True).ok
    snap = chain.counter.snapshot()
    assert snap["calls"] == 4 and sorted(snap["by_day"].values()) == [1, 3]


# ======================================================================================================================
# token bucket
# ======================================================================================================================
def test_token_bucket_serves_the_burst_then_paces_at_the_rate():
    c = Clock(0.0)
    b = H.TokenBucket(2.0, burst=2.0, clock=c.now, sleep=c.sleep)
    assert [b.acquire() for _ in range(2)] == [0.0, 0.0]
    assert b.acquire() == pytest.approx(0.5) and c.t == pytest.approx(0.5)
    assert b.acquire() == pytest.approx(0.5)


def test_token_bucket_long_run_rate_never_exceeds_the_limit():
    c = Clock(0.0)
    b = H.TokenBucket(2.0, burst=2.0, clock=c.now, sleep=c.sleep)
    for _ in range(200):
        b.acquire()
    assert (200 - 2) / 2.0 - 1e-9 <= c.t <= 200 / 2.0


def test_token_bucket_refills_but_never_beyond_burst():
    c = Clock(0.0)
    b = H.TokenBucket(1.0, burst=3.0, clock=c.now, sleep=c.sleep)
    assert all(b.try_acquire() for _ in range(3)) and not b.try_acquire()
    c.t += 100.0
    assert [b.try_acquire() for _ in range(4)] == [True, True, True, False]


def test_token_bucket_rejects_nonsense():
    for kw in ({"rate_per_s": 0}, {"rate_per_s": -1}, {"rate_per_s": 1, "burst": 0.5}):
        with pytest.raises(ValueError):
            H.TokenBucket(**kw)


def test_rpc_client_waits_on_the_bucket_and_the_method_bucket():
    c = Clock(0.0)
    node = FakeNode({"getAccountInfo": [account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]})
    rpc = H.RpcClient(H.RpcConfig.public(), bucket=H.TokenBucket(2.0, burst=1.0, clock=c.now, sleep=c.sleep),
                      method_buckets={"getAccountInfo": H.TokenBucket(0.5, burst=1.0, clock=c.now, sleep=c.sleep)},
                      post=node, sleep=c.sleep, now_ms=c.now_ms)
    for _ in range(3):
        rpc.call("getAccountInfo", [MINT])
    assert c.t >= 4.0 - 1e-9  # the method bucket (one per 2 s) is the binding one
    assert rpc.counter.snapshot()["throttle_wait_s"] > 0


def test_default_client_is_paced_at_two_per_second():
    rpc = H.RpcClient(H.RpcConfig.public(), post=FakeNode({"getAccountInfo": [account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]}))
    assert rpc.bucket.rate == 2.0 and rpc.bucket.burst == 2.0 and rpc.daily_call_cap == 40_000


# ======================================================================================================================
# credit counter
# ======================================================================================================================
def test_credit_counter_public_counts_calls_not_credits_and_helius_counts_both():
    c = H.CreditCounter("public", now_ms=lambda: 1_760_000_000_000)
    c.record_call("getAccountInfo")
    c.record_call("getTokenLargestAccounts")
    assert c.snapshot()["calls"] == 2 and c.snapshot()["credits"] == 0
    h = H.CreditCounter("helius", now_ms=lambda: 1_760_000_000_000)
    for _ in range(5):
        h.record_call("getAccountInfo")
    s = h.snapshot()
    assert s["calls"] == 5 and s["credits"] == 5 and s["by_method"] == {"getAccountInfo": 5} and s["provider"] == "helius"
    assert s["schema"] == "hv4_chain.v1"


def test_credit_report_includes_cache_numbers():
    chain, _, _ = make_chain({"getAccountInfo": [account_reply(build_base(), H.SPL_TOKEN_PROGRAM)]})
    chain.mint_facts(MINT)
    chain.mint_facts(MINT)
    r = chain.credit_report()
    assert r["calls"] == 1 and r["cache_hits"] == 1 and r["cache_entries"] == 1 and r["cache_misses_total"] == 1


# ======================================================================================================================
# endpoint choice and key hygiene
# ======================================================================================================================
FAKE_KEY = "fakekey_ABCDEF0123456789"


def test_default_is_public_and_url_is_never_shown():
    cfg = H.RpcConfig.from_env({})
    assert cfg.provider == "public" and repr(cfg) == "RpcConfig(provider='public')"
    assert cfg._url == H.PUBLIC_RPC


def test_helius_key_is_read_from_the_env_file_and_never_appears_in_text(tmp_path, caplog):
    envf = tmp_path / "helius.env"
    envf.write_text(f"# comment\nOTHER=1\nHELIUS_API_KEY={FAKE_KEY}\n")
    cfg = H.RpcConfig.helius(env={}, env_files=[envf])
    assert cfg.provider == "helius" and FAKE_KEY not in repr(cfg) and FAKE_KEY not in str(cfg)
    assert H.RpcConfig.from_env({"MAL_HV4_RPC": "helius"}, [envf]).provider == "helius"
    assert H.RpcConfig.from_env({"MAL_HV4_RPC": "public"}, [envf]).provider == "public"
    boom = [urllib.error.URLError(f"cannot reach https://mainnet.helius-rpc.com/?api-key={FAKE_KEY}"),
            ValueError(f"bad {FAKE_KEY}"), RuntimeError(FAKE_KEY)]
    clock = Clock()
    node = FakeNode({"getAccountInfo": boom})
    rpc = H.RpcClient(cfg, bucket=H.TokenBucket(1000, burst=1000, clock=clock.now, sleep=clock.sleep), post=node, sleep=clock.sleep,
                      now_ms=clock.now_ms)
    chain = H.HV4Chain(rpc, H.TtlCache(clock=clock.now), now_ms=clock.now_ms)
    with caplog.at_level(logging.DEBUG, logger="hv4_chain"):
        facts = [chain.mint_facts(MINT, force=True) for _ in range(3)]
        with pytest.raises(H.ChainError) as ei:
            rpc.call("getAccountInfo", [MINT])
    blob = " ".join([repr(f) for f in facts] + [str(ei.value), repr(rpc), caplog.text, json.dumps(chain.credit_report())])
    assert FAKE_KEY not in blob and "api-key" not in blob and "helius-rpc" not in blob
    rep = chain.credit_report()
    assert rep["provider"] == "helius" and rep["credits"] == rep["calls"] > 0


def test_helius_key_from_environment_beats_the_file(tmp_path):
    envf = tmp_path / "helius.env"
    envf.write_text("HELIUS_API_KEY=filekey_ABCDEF0123456789\n")
    cfg = H.RpcConfig.helius(env={"HELIUS_API_KEY": FAKE_KEY}, env_files=[envf])
    assert FAKE_KEY in cfg._url and "filekey" not in cfg._url


@pytest.mark.parametrize("content", ["", "HELIUS_API_KEY=\n", "HELIUS_API_KEY=short\n", "HELIUS_API_KEY=has space in it 1234\n",
                                     "HELIUS_API_KEY=abc&def=1234567890\n", "HELIUS_API_KEY=a\nb=1\n"])
def test_helius_missing_or_malformed_key_fails_closed(tmp_path, content):
    envf = tmp_path / "helius.env"
    envf.write_text(content)
    with pytest.raises(H.ChainError) as ei:
        H.RpcConfig.helius(env={}, env_files=[envf])
    assert str(ei.value) == "helius_key_missing_or_malformed"
    with pytest.raises(H.ChainError):
        H.RpcConfig.helius(env={}, env_files=[tmp_path / "missing.env"])


def test_redact_removes_urls_keys_and_known_values():
    s = H.redact(f"GET https://x.helius-rpc.com/?api-key={FAKE_KEY} failed; also {FAKE_KEY} and api_key=zzz", [FAKE_KEY])
    assert FAKE_KEY not in s and "https" not in s and "zzz" not in s


# ======================================================================================================================
# largest_holders
# ======================================================================================================================
def _holder_rows(entries: list[tuple[str, int]]) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 900}, "value": [
        {"address": a, "amount": str(n), "decimals": 6, "uiAmount": n / 1e6, "uiAmountString": str(n / 1e6)} for a, n in entries]}}


def _supply_reply(n: int) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 900}, "value": {"amount": str(n), "decimals": 6}}}


def _wallet(i: int) -> str:
    return M.b58encode(bytes([i]) * 31 + bytes([200]))


def _holder_chain(entries, supply=1_000_000, mint_reply=None, **kw):
    script = {"getTokenLargestAccounts": [_holder_rows(entries)], "getTokenSupply": [_supply_reply(supply)],
              "getAccountInfo": [mint_reply or account_reply(build_base(supply=supply), H.SPL_TOKEN_PROGRAM)]}
    return make_chain(script, **kw)


def test_holders_exclude_pool_vault_curve_and_burn_and_share_of_supply():
    tp = H.SPL_TOKEN_PROGRAM
    vault = H.associated_token_address(POOL, tp, MINT)
    curve_ata = H.associated_token_address(H.bonding_curve_pda(MINT), tp, MINT)
    burn_atas = [H.associated_token_address(o, tp, MINT) for o in H.BURN_OWNERS]
    others = [(_wallet(i), 10_000 - i * 100) for i in range(1, 13)]
    entries = [(vault, 600_000), (curve_ata, 5), (burn_atas[0], 40_000), (burn_atas[1], 3)] + others
    chain, node, _ = _holder_chain(entries)
    h = chain.largest_holders(MINT, pool=POOL)
    assert h.ok and h.n_listed == 16 and h.n_excluded == 4 and h.n_counted == 12 and h.listing_complete
    assert h.top1_frac == pytest.approx(9_900 / 1_000_000)
    assert h.top10_frac == pytest.approx(sum(a for _, a in others[:10]) / 1_000_000)
    assert h.excluded_frac == pytest.approx((600_000 + 5 + 40_000 + 3) / 1_000_000)
    assert h.top1_float_frac == pytest.approx(9_900 / (1_000_000 - 640_008))
    assert h.top10_float_frac == pytest.approx(sum(a for _, a in others[:10]) / (1_000_000 - 640_008))
    assert len(h.top10) == 10 and h.top10[0] == (others[0][0], 9_900)
    assert h.as_of_slot == 900 and h.as_of_ms
    card = h.to_card_fields()
    assert card["top1_frac"] == h.top1_frac and card["holders_ok"] is True


def test_holders_explicit_pool_vault_and_extra_exclusions():
    odd_vault = _wallet(77)
    entries = [(odd_vault, 500_000), (_wallet(1), 1_000), (_wallet(2), 900), (_wallet(3), 800)]
    chain, _, _ = _holder_chain(entries)
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM, pool_vault=odd_vault)
    assert h.n_excluded == 1 and h.top1_frac == pytest.approx(0.001)
    chain, _, _ = _holder_chain(entries)
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM, exclude_accounts=[odd_vault, _wallet(1)])
    assert h.n_excluded == 2 and h.top1_frac == pytest.approx(0.0009)
    owner = _wallet(50)
    ata = H.associated_token_address(owner, H.SPL_TOKEN_PROGRAM, MINT)
    chain, _, _ = _holder_chain([(ata, 700_000), (_wallet(1), 1_000)])
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM, exclude_owners=[owner])  # an owner is excluded through its ATA
    assert h.n_excluded == 1 and h.top1_frac == pytest.approx(0.001)


def test_holders_token_2022_uses_the_token_2022_ata():
    tp = H.TOKEN_2022_PROGRAM
    vault = H.associated_token_address(POOL, tp, MINT)
    assert vault != H.associated_token_address(POOL, H.SPL_TOKEN_PROGRAM, MINT)
    chain, _, _ = _holder_chain([(vault, 900_000), (_wallet(1), 1_000)],
                                mint_reply=account_reply(build_t22([(18, v_metadata_pointer())], base=build_base(supply=1_000_000)), tp))
    h = chain.largest_holders(MINT, pool=POOL)  # token program comes from mint_facts
    assert h.ok and h.n_excluded == 1 and h.top1_frac == pytest.approx(0.001)


def test_holders_on_the_recorded_list_exclude_the_pool_vault():
    hd = REC["holders"]
    pool = next(p for p, e in REC_POOLS.items() if e["mint"] == hd["mint"])
    tp = REC_MINTS[hd["mint"]]["owner"]
    rows = {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": hd["slot"]}, "value": hd["value"]}}
    node = FakeNode({"getTokenLargestAccounts": [rows], "getTokenSupply": [_supply_reply(int(hd["supply"]))]})
    clock = Clock()
    rpc = H.RpcClient(H.RpcConfig.public(), bucket=H.TokenBucket(1000, burst=1000, clock=clock.now, sleep=clock.sleep), post=node,
                      sleep=clock.sleep, now_ms=clock.now_ms)
    chain = H.HV4Chain(rpc, H.TtlCache(clock=clock.now), now_ms=clock.now_ms)
    h = chain.largest_holders(hd["mint"], token_program=tp, pool=pool)
    assert hd["owners"][hd["value"][0]["address"]] == pool  # the recorded top account really is the pool's
    assert h.ok and h.n_listed == 20 and h.n_excluded == 1 and h.n_counted == 19 and not h.listing_complete
    assert h.top1_frac == pytest.approx(int(hd["value"][1]["amount"]) / int(hd["supply"]))
    assert h.excluded_frac > 0.99 and h.top10_frac < 0.01
    assert h.top1_float_frac is not None and h.top1_float_frac > h.top1_frac
    # every recorded token account is the ATA of its owner, so deriving the vault by ATA is sound
    for addr, owner in list(hd["owners"].items())[:5]:
        assert H.associated_token_address(owner, tp, hd["mint"]) == addr


def test_holders_cache_card_time_bypass_and_supply_cache():
    chain, node, clock = _holder_chain([(_wallet(1), 1_000), (_wallet(2), 900)])
    sp = H.SPL_TOKEN_PROGRAM
    a = chain.largest_holders(MINT, token_program=sp)
    assert chain.largest_holders(MINT, token_program=sp) is a and node.n("getTokenLargestAccounts") == 1
    clock.t += 899
    assert chain.largest_holders(MINT, token_program=sp) is a
    clock.t += 2  # past the 15-minute refresh
    b = chain.largest_holders(MINT, token_program=sp)
    assert b is not a and node.n("getTokenLargestAccounts") == 2 and node.n("getTokenSupply") == 1  # supply is cached for an hour
    c = chain.largest_holders(MINT, token_program=sp, max_age_s=0)  # card time
    assert c is not b and node.n("getTokenLargestAccounts") == 3
    d = chain.largest_holders(MINT, token_program=sp, pool=POOL)  # a different exclusion set is a different entry
    assert d is not c and node.n("getTokenLargestAccounts") == 4


def test_holders_inconsistent_supply_rereads_supply_once_then_fails_closed():
    entries = [(_wallet(1), 600_000), (_wallet(2), 500_000)]
    script = {"getTokenLargestAccounts": [_holder_rows(entries)], "getTokenSupply": [_supply_reply(1_000_000), _supply_reply(2_000_000)]}
    chain, node, _ = make_chain(script)
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM)
    assert h.ok and node.n("getTokenSupply") == 2 and h.top1_frac == pytest.approx(0.3)
    script = {"getTokenLargestAccounts": [_holder_rows(entries)], "getTokenSupply": [_supply_reply(1_000_000)]}
    chain, node, _ = make_chain(script)
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM)
    assert h.ok is False and h.error == "inconsistent_supply" and node.n("getTokenSupply") == 2 and h.top10_frac is None


def test_holders_top10_incomplete_fails_closed_when_the_list_is_truncated():
    vault = H.associated_token_address(POOL, H.SPL_TOKEN_PROGRAM, MINT)
    entries = [(vault, 900_000)] + [(_wallet(i), 100 - i) for i in range(1, 10)] + [(_wallet(100 + i), 50 - i) for i in range(10)]
    chain, _, _ = _holder_chain(entries)  # 20 listed, 1 excluded, 19 counted: fine
    assert chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM, pool=POOL).ok
    ex = [_wallet(150 + i) for i in range(12)]  # 20 listed but 12 excluded leaves 8 counted with more beyond the list
    entries = [(a, 10_000) for a in ex] + [(_wallet(i), 100 - i) for i in range(1, 9)]
    chain, _, _ = _holder_chain(entries)
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM, exclude_accounts=ex)
    assert h.ok is False and h.error == "top10_incomplete"


def test_holders_short_complete_list_is_fine_and_empty_list_means_zero():
    chain, _, _ = _holder_chain([(_wallet(1), 5), (_wallet(2), 4)])
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM)
    assert h.ok and h.listing_complete and h.n_counted == 2 and h.top10_frac == pytest.approx(9 / 1_000_000)
    chain, _, _ = _holder_chain([])
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM)
    assert h.ok and h.top1_frac == 0.0 and h.top10_frac == 0.0 and h.n_counted == 0


@pytest.mark.parametrize("entries,supply", [
    ([(_wallet(1), 5), (_wallet(1), 4)], 1000),  # duplicate address
    ([("notakey", 5)], 1000),
    ([(_wallet(1), 5)], 0),  # zero supply
    ([(_wallet(i), 5) for i in range(1, 22)], 10_000),  # more than 20 rows
])
def test_holders_malformed_answers_fail_closed(entries, supply):
    chain, _, _ = _holder_chain(entries, supply=supply)
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM)
    assert h.ok is False and h.error and h.top1_frac is None


@pytest.mark.parametrize("amt", ["-5", "1.5", "", "abc", 12, None, "9" * 30])
def test_holders_bad_amount_values_fail_closed(amt):
    rows = _holder_rows([(_wallet(1), 5)])
    rows["result"]["value"][0]["amount"] = amt
    chain, _, _ = make_chain({"getTokenLargestAccounts": [rows], "getTokenSupply": [_supply_reply(1000)]})
    assert chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM).ok is False


def test_holders_rpc_error_fails_closed():
    chain, _, _ = make_chain({"getTokenLargestAccounts": [rpc_error(-32005)], "getTokenSupply": [_supply_reply(1000)]}, attempts=1)
    h = chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM)
    assert h.ok is False and h.error == "rpc_-32005"


def test_holders_without_mint_facts_fail_closed_and_spend_no_holder_call():
    chain, node, _ = make_chain({"getAccountInfo": [rpc_error(-32602)]}, attempts=1)
    h = chain.largest_holders(MINT)  # token program unknown because mint_facts failed
    assert h.ok is False and h.error.startswith("mint_facts:") and node.n("getTokenLargestAccounts") == 0


def test_holders_bad_arguments_make_no_call():
    chain, node, _ = _holder_chain([(_wallet(1), 5)])
    assert chain.largest_holders(MINT, token_program="11111111111111111111111111111111").ok is False
    assert chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM, pool="nope").ok is False
    assert chain.largest_holders("nope").error == "bad_mint_address"
    assert node.calls == []


# ======================================================================================================================
# Mayhem flag
# ======================================================================================================================
def _pool_reply(mint: str, mayhem: int, slot: int = 600) -> dict:
    data = bytearray(rec_bytes(next(iter(REC_POOLS.values()))))
    data[H.POOL_BASE_MINT: H.POOL_BASE_MINT + 32] = M.b58decode(mint)
    data[H.POOL_MAYHEM] = mayhem
    return account_reply(bytes(data), H.PUMPSWAP_PROGRAM, slot=slot)


def test_mayhem_tape_flag_is_used_without_any_call():
    chain, node, _ = make_chain({"getAccountInfo": [_pool_reply(MINT, 1)]})
    f = chain.mayhem_flag(MINT, tape_is_mayhem=False, pool=POOL)
    assert f.is_mayhem is False and f.source == "tape" and not f.counts_as_mayhem
    f = chain.mayhem_flag(MINT, tape_is_mayhem=True, pool=POOL)
    assert f.is_mayhem is True and f.counts_as_mayhem
    assert node.calls == []


def test_mayhem_falls_back_to_the_pool_account_and_caches_it():
    chain, node, _ = make_chain({"getAccountInfo": [_pool_reply(MINT, 1)]})
    f = chain.mayhem_flag(MINT, pool=POOL)
    assert f.is_mayhem is True and f.source == "pool_account" and f.as_of_slot == 600
    chain, node, _ = make_chain({"getAccountInfo": [_pool_reply(MINT, 0)]})
    f = chain.mayhem_flag(MINT, pool=POOL)
    assert f.is_mayhem is False and f.counts_as_mayhem is False
    chain.mayhem_flag(MINT, pool=POOL)
    assert node.n("getAccountInfo") == 1  # the flag never changes: cached


def test_mayhem_unknown_counts_as_mayhem():
    chain, node, _ = make_chain({"getAccountInfo": [_pool_reply(MINT, 0)]})
    f = chain.mayhem_flag(MINT)  # no tape flag and no pool
    assert f.is_mayhem is None and f.source == "unknown" and f.counts_as_mayhem and node.calls == []
    for bad in (1, 0, "false", "true"):  # only a real bool is a tape flag
        assert chain.mayhem_flag(MINT, tape_is_mayhem=bad).counts_as_mayhem  # type: ignore[arg-type]


@pytest.mark.parametrize("reply", [
    rpc_error(-32005),
    {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 5}, "value": None}},
    account_reply(b"\0" * 400, H.PUMPSWAP_PROGRAM),
    account_reply(b"\0" * 400, H.SPL_TOKEN_PROGRAM),
])
def test_mayhem_pool_failures_leave_the_flag_unknown(reply):
    chain, _, _ = make_chain({"getAccountInfo": [reply]}, attempts=1)
    f = chain.mayhem_flag(MINT, pool=POOL)
    assert f.is_mayhem is None and f.counts_as_mayhem and f.error


def test_mayhem_pool_for_another_mint_is_rejected():
    chain, _, _ = make_chain({"getAccountInfo": [_pool_reply(_wallet(5), 0)]})
    f = chain.mayhem_flag(MINT, pool=POOL)
    assert f.is_mayhem is None and f.counts_as_mayhem and "base mint" in f.error


def test_mayhem_verify_reports_a_conflict_as_mayhem():
    chain, _, _ = make_chain({"getAccountInfo": [_pool_reply(MINT, 1)]})
    f = chain.mayhem_flag(MINT, tape_is_mayhem=False, pool=POOL, verify=True)
    assert f.is_mayhem is True and f.source == "conflict" and f.counts_as_mayhem
    chain, _, _ = make_chain({"getAccountInfo": [_pool_reply(MINT, 0)]})
    f = chain.mayhem_flag(MINT, tape_is_mayhem=False, pool=POOL, verify=True)
    assert f.is_mayhem is False and f.source == "pool_account"
    chain, _, _ = make_chain({"getAccountInfo": [rpc_error(-32005)]}, attempts=1)
    f = chain.mayhem_flag(MINT, tape_is_mayhem=False, pool=POOL, verify=True)
    assert f.is_mayhem is False and f.source == "tape" and f.error  # tape value kept, the failed check is reported


def test_mayhem_veto_by_age():
    yes = H.MayhemFlag(MINT, True, "tape")
    no = H.MayhemFlag(MINT, False, "tape")
    unknown = H.MayhemFlag(MINT, None, "unknown")
    day = H.MAYHEM_VETO_AGE_S
    assert H.mayhem_veto(yes, day - 1) and not H.mayhem_veto(yes, day) and H.mayhem_veto(yes, None)
    assert H.mayhem_veto(unknown, 100) and H.mayhem_veto(unknown, None) and not H.mayhem_veto(unknown, day + 1)
    assert not H.mayhem_veto(no, 0) and not H.mayhem_veto(no, None)
    assert yes.to_card_fields()["mayhem_counts"] is True


# ======================================================================================================================
# isolation and hygiene
# ======================================================================================================================
def test_module_imports_nothing_from_the_banned_families_and_touches_no_sealed_path():
    src = (Path(__file__).parent / "hv4_chain.py").read_text()
    mods: set[str] = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
            mods.update(f"{n.module}.{a.name}" for a in n.names)
    assert [m for m in mods if any(t in m.lower() for t in ("l3", "r4b", "laya"))] == []
    for sealed in ("forward-", "truth-", "fresh-08", "exp011-", "tip-tape-archive", "/var/lib/mal/sealed", "meta-capture", "/data/mal/blocks"):
        assert sealed not in src


def test_module_has_no_signing_or_sending_surface():
    src = (Path(__file__).parent / "hv4_chain.py").read_text()
    for word in ("sendTransaction", "signTransaction", "simulateTransaction", "Keypair", "private_key"):
        assert word not in src


def test_only_read_methods_are_called():
    src = (Path(__file__).parent / "hv4_chain.py").read_text()
    called = {n.args[0].value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "call" and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}
    assert called == {"getAccountInfo", "getTokenSupply", "getTokenLargestAccounts"}


def test_fixture_file_holds_no_url_or_key():
    text = FX_PATH.parent.joinpath("recorded.json").read_text().lower()
    assert "api-key" not in text and "helius-rpc" not in text and "http://" not in text and "https://" not in text
