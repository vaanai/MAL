"""Offline tests: on-chain uri resolution, metadata fetch with reasons, enrich -> cards."""
import base64
import json
import struct

from tools.gradscore import card as cd, enrich as en, hist as hs
from tools.gradscore.tests.test_hist import M, T0, BASE, VM, mk_block, allow  # noqa: F401  (fixture re-export)

MINTS = ["So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
         "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"]


def metaplex_blob(name, symbol, uri):
    def s(x, n):
        b = x.encode().ljust(n, b"\x00")
        return struct.pack("<I", n) + b
    return bytes([4]) + b"\x01" * 32 + b"\x02" * 32 + s(name, 32) + s(symbol, 10) + s(uri, 200)


class FakeRpc:
    def __init__(self):
        self.calls = 0
        self.pdas = {en.pda(MINTS[0]): metaplex_blob("Frog", "FROG", "https://ipfs.io/ipfs/bafyfrog")}

    def call(self, method, params):
        self.calls += 1
        keys, cfg = params
        if cfg["encoding"] == "base64":
            return {"value": [{"data": [base64.b64encode(self.pdas[k]).decode(), "base64"]} if k in self.pdas else None for k in keys]}
        out = []
        for k in keys:
            if k == MINTS[1]:
                out.append({"data": {"parsed": {"info": {"extensions": [{"extension": "tokenMetadata", "state": {"name": "T22", "symbol": "T", "uri": "https://ipfs.io/ipfs/bafyt22"}}]}}}})
            elif k == MINTS[2]:
                out.append({"data": {"parsed": {"info": {"extensions": []}}}})
            else:
                out.append(None)
        return {"value": out}


def test_parse_metaplex_strips_padding_and_rejects_garbage():
    assert en.parse_metaplex(metaplex_blob("Frog", "FROG", "https://x/y")) == {"name": "Frog", "symbol": "FROG", "uri": "https://x/y"}
    assert en.parse_metaplex(b"\x00" * 10) is None
    assert en.parse_metaplex(bytes([4]) + b"\x00" * 64 + struct.pack("<I", 9999)) is None


def test_resolve_metaplex_then_token2022_then_none():
    r = en.resolve(MINTS, FakeRpc())
    assert r[MINTS[0]]["source"] == "metaplex" and r[MINTS[0]]["uri"].endswith("bafyfrog")
    assert r[MINTS[1]]["source"] == "token2022" and r[MINTS[1]]["uri"].endswith("bafyt22")
    assert r[MINTS[2]]["uri"] is None and r[MINTS[2]]["rpc_status"] == "no_uri"


def test_rpc_retries_and_gives_up():
    n = []

    def opener(url, body, timeout):
        n.append(1)
        raise OSError("429")

    rpc = en.Rpc(opener=opener, sleep=lambda s: None, retries=3)
    assert rpc.call("x", []) is None and len(n) == 3
    r = en.resolve(MINTS[:1], rpc)
    assert r[MINTS[0]]["rpc_status"] == "rpc_fail"


def test_fetch_reasons_and_retry_once_at_end(tmp_path):
    state = {"n": 0}

    def opener(url, timeout):
        if "bafyfrog" in url:
            state["n"] += 1
            if state["n"] == 1:  # first round fails for every url, the end-of-run retry succeeds
                raise OSError("t")
            if state["n"] <= 3:
                raise OSError("t")
            return json.dumps({"description": "A frog", "twitter": "https://x.com/frog", "junk": [1]})
        if "bafyt22" in url:
            return "<html>"
        raise OSError("down")

    f = hs.MetaFetcher(str(tmp_path / "mc"), opener=opener, sleep=lambda s: None)
    cards = tmp_path / "cards-blk.jsonl"
    cards.write_text("".join(json.dumps({"mint": m, "uri": None}) + "\n" for m in MINTS))
    res = hs.cmd_enrich(str(cards), str(tmp_path / "o"), rpc=FakeRpc(), fetcher=f, workers=1)
    assert res["mints"] == 3 and res["with_uri"] == 2 and res["with_metadata"] == 1 and res["retried"] == 2
    assert res["status"] == {"ok": 1, "not_json": 1, "no_uri:no_uri": 1}
    rows = {json.loads(l)["mint"]: json.loads(l) for l in open(tmp_path / "o" / "enrich-blk.jsonl")}
    assert rows[MINTS[0]]["meta"] == {"description": "A frog", "twitter": "https://x.com/frog"}


def test_cards_rebuilt_from_enrichment_have_description(allow):  # noqa: F811
    blk = mk_block(allow, BASE)
    enr = allow / "enrich-blk.jsonl"
    enr.write_text(json.dumps({"mint": "MINTX", "uri": "https://ipfs.io/ipfs/bafyfrog", "meta": {"description": "A frog in a suit", "twitter": "https://x.com/frogsol"},
                               "fetch_status": "ok", "name": None, "symbol": None}) + "\n")
    hs.cmd_cards(str(blk), str(allow / "o"), workers=1, vmap=VM, enrich=str(enr))
    row = json.loads(open(allow / "o" / "cards-blk.jsonl").readline())
    c = json.loads(row["card_text"])
    assert row["has_uri"] and row["has_metadata"] and c["description"] == "A frog in a suit" and c["twitter_handle"] == "frogsol" and c["has_twitter"]
    assert set(c) == set(cd.build_card({}, {}, {}, None, None))


def test_cards_without_metadata_still_written_and_flagged(allow):  # noqa: F811
    blk = mk_block(allow, BASE)
    enr = allow / "enrich-blk.jsonl"
    enr.write_text(json.dumps({"mint": "MINTX", "uri": "https://x/y", "meta": None, "fetch_status": "fetch_fail"}) + "\n")
    hs.cmd_cards(str(blk), str(allow / "o"), workers=1, vmap=VM, enrich=str(enr))
    row = json.loads(open(allow / "o" / "cards-blk.jsonl").readline())
    assert row["has_uri"] is True and row["has_metadata"] is False
