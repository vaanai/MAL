"""Tests for tools/mint_account_fetch.py with a mocked RPC. No network, no env file."""

from __future__ import annotations

import base64
import json
import struct

import pytest

from tools import mint_account_fetch as m

M1 = "9VEpuoCRJxHddJmgzuHHCSbGoXaubRSaVu8jNxKcpump"
M2 = "A9DtkpXBcz2o2K3iG7xniDbWktvHRtEbq7XNyNBvpump"
KEY = "TESTKEY123"
URL = m.rpc_url(KEY)


def parsed(owner, info, program):
    return {"context": {"slot": 100}, "value": {"owner": owner, "data": {"program": program, "parsed": {"type": "mint", "info": info}}}}


def mock(table):
    calls = []

    def post(url, payload):
        calls.append(payload)
        r = table[(payload["params"][0], payload["params"][1]["encoding"])]
        return r if "error" in r else {"result": r}
    return post, calls


def fetcher(post):
    return m.Fetcher(URL, post=post, sleep=lambda s: None, secrets=[KEY])


def test_parsed_token2022_ext():
    info = {"mintAuthority": None, "freezeAuthority": None, "decimals": 6, "supply": "5",
            "extensions": [{"extension": "metadataPointer", "state": {}}, {"extension": "tokenMetadata", "state": {}}]}
    post, calls = mock({(M1, "jsonParsed"): parsed(m.TOKEN_2022_PROGRAM, info, "spl-token-2022")})
    r = m.fetch_one(fetcher(post), M1)
    assert r["fetch_ok"] and r["owner_program"] == m.TOKEN_2022_PROGRAM and r["fetch_slot"] == 100
    assert r["token2022_extensions"] == ["metadataPointer", "tokenMetadata"] and r["freeze_authority"] is None and r["decimals"] == 6
    assert len(calls) == 1


def test_spl_token_freeze():
    info = {"mintAuthority": "A" * 32, "freezeAuthority": "F" * 32, "decimals": 6, "supply": "1"}
    post, _ = mock({(M1, "jsonParsed"): parsed(m.SPL_TOKEN_PROGRAM, info, "spl-token")})
    r = m.fetch_one(fetcher(post), M1)
    assert r["freeze_authority"] == "F" * 32 and r["token2022_extensions"] == []


def _mint_bytes(freeze=None, ext_types=()):
    b = bytearray(82)
    b[44] = 6
    struct.pack_into("<Q", b, 36, 777)
    if freeze:
        struct.pack_into("<I", b, 46, 1)
        b[50:82] = freeze
    if ext_types:
        b += bytes(165 - len(b)) + bytes([1])
        for t in ext_types:
            b += struct.pack("<HH", t, 4) + b"\0" * 4
    return bytes(b)


def test_base64_fallback_parses_extensions_and_freeze():
    raw = _mint_bytes(freeze=bytes([1]) * 32, ext_types=(18, 19, 1))
    enc = base64.b64encode(raw).decode()
    unparsed = {"context": {"slot": 54}, "value": {"owner": m.TOKEN_2022_PROGRAM, "data": [enc, "base64"]}}
    b64 = {"context": {"slot": 55}, "value": {"owner": m.TOKEN_2022_PROGRAM, "data": [enc, "base64"]}}
    post, calls = mock({(M1, "jsonParsed"): unparsed, (M1, "base64"): b64})
    r = m.fetch_one(fetcher(post), M1)
    assert r["fetch_ok"] and r["encoding"] == "base64" and r["fetch_slot"] == 55 and len(calls) == 2
    assert r["token2022_extensions"] == ["metadataPointer", "tokenMetadata", "transferFeeConfig"]
    assert r["freeze_authority"] and r["supply"] == "777" and r["decimals"] == 6 and r["mint_authority"] is None


def test_not_found_is_error_not_ok():
    post, _ = mock({(M1, "jsonParsed"): {"context": {"slot": 1}, "value": None}})
    r = m.fetch_one(fetcher(post), M1)
    assert not r["fetch_ok"] and "not found" in r["error"]


def test_retry_three_then_fail_and_redaction():
    n = {"c": 0}

    def post(url, payload):
        n["c"] += 1
        raise OSError(f"boom {url} {KEY}")
    f = fetcher(post)
    r = m.fetch_one(f, M1)
    assert n["c"] == 3 and f.calls == 3 and not r["fetch_ok"]
    dumped = json.dumps(r)
    assert KEY not in dumped and "mainnet" not in dumped and "api-key=" not in dumped


def test_retry_recovers():
    seq = iter([OSError("x"), {"result": parsed(m.SPL_TOKEN_PROGRAM, {"decimals": 6, "supply": "1"}, "spl-token")}])

    def post(url, payload):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v
    f = fetcher(post)
    assert m.fetch_one(f, M1)["fetch_ok"] and f.calls == 2


def test_rpc_error_response_retries():
    post, calls = mock({(M1, "jsonParsed"): {"error": {"code": 429, "message": "x"}}})
    r = m.fetch_one(fetcher(post), M1)
    assert len(calls) == 3 and "429" in r["error"]


def test_rate_limit_spacing():
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s
    post, _ = mock({(M1, "jsonParsed"): parsed(m.SPL_TOKEN_PROGRAM, {"decimals": 6, "supply": "1"}, "spl-token")})
    f = m.Fetcher(URL, post=post, sleep=sleep, clock=lambda: t["now"])
    for _ in range(11):
        m.fetch_one(f, M1)
    assert t["now"] >= 1.0 - 1e-9  # 11 calls need 10 gaps of 0.1 s, so <= 10 rps


def test_run_census_and_members_outputs(tmp_path):
    ext = {"decimals": 6, "supply": "1", "freezeAuthority": None, "extensions": [{"extension": "metadataPointer"}]}
    post, _ = mock({(M1, "jsonParsed"): parsed(m.TOKEN_2022_PROGRAM, ext, "spl-token-2022"),
                    (M2, "jsonParsed"): parsed(m.SPL_TOKEN_PROGRAM, {"decimals": 6, "supply": "1", "freezeAuthority": "F" * 32}, "spl-token")})
    s = m.run("census", [M1, M2], tmp_path / "c", fetcher(post))
    assert s["owner_program_counts"] == {m.TOKEN_2022_PROGRAM: 1, m.SPL_TOKEN_PROGRAM: 1}
    assert s["extension_type_frequency"] == {"metadataPointer": 1} and s["freeze_authority_present"] == 1
    assert s["error_count"] == 0 and s["calls"] == 2 and s["credits_approx"] == 2
    assert len((tmp_path / "c" / "mints.jsonl").read_text().splitlines()) == 2
    assert not (tmp_path / "c" / "mint_accounts.json").exists()
    post, _ = mock({(M1, "jsonParsed"): parsed(m.TOKEN_2022_PROGRAM, ext, "spl-token-2022")})
    m.run("members", [M1], tmp_path / "m", fetcher(post))
    doc = json.loads((tmp_path / "m" / "mint_accounts.json").read_text())
    assert doc["fetch_slot"] == 100
    assert doc["mints"][M1] == {"fetch_ok": True, "owner_program": m.TOKEN_2022_PROGRAM, "freeze_authority": None,
                                "token2022_extensions": ["metadataPointer"]}


def test_load_key_env_and_file(tmp_path):
    assert m.load_key({"HELIUS_API_KEY": "abc"}) == "abc"
    p = tmp_path / "h.env"
    p.write_text("# c\nexport HELIUS_API_KEY='k_1-2'\n")
    assert m.load_key({}, p) == "k_1-2"
    with pytest.raises(m.FetchError):
        m.load_key({}, tmp_path / "missing")
    p.write_text("HELIUS_API_KEY=bad key&x\n")
    with pytest.raises(m.FetchError):
        m.load_key({}, p)


def test_read_mints(tmp_path):
    p = tmp_path / "i"
    p.write_text(f"{M1}\n# c\n{json.dumps({'mint': M2})}\n{M1}\n")
    assert m.read_mints(p) == [M1, M2]
    p.write_text("not-a-mint!\n")
    with pytest.raises(ValueError):
        m.read_mints(p)
