import json
from pathlib import Path

from tools import ipfs_fetch as ipf
from tools import p28h_stage0d as s0d

LOCAL = ipf.parse_local_gateway("http://127.0.0.1:8080", 1000.0, 1000.0, 1.0, 1.0)
CID = "bafkreianunjaagczts6hsevkgw6wwu66ruzm6cqxls2kxbpfarlc3knqma"


def test_parse_meta_fields_and_non_object():
    f = s0d.parse_meta(b'{"name":"A","description":"d","twitter":"x","extra":1}')
    assert f["name"] == "A" and f["website"] == "" and "extra" not in f
    assert s0d.parse_meta(b"[1]") is None and s0d.parse_meta(b"nope") is None


def test_fetch_one_local_only_and_non_ipfs():
    seen = []

    def http(url, *a):
        seen.append(url)
        return (200, b'{"name":"N"}')

    lim = s0d.Limiter(1000.0)
    st, f, h = s0d.fetch_one("https://ipfs.io/ipfs/" + CID, LOCAL, http, lim)
    assert st == "ok" and f["name"] == "N" and h
    assert seen == ["http://127.0.0.1:8080/ipfs/" + CID]
    seen.clear()
    assert s0d.fetch_one("https://example.com/x.json", LOCAL, http, lim)[0] == "non_ipfs"
    assert seen == []


def test_retry_then_fail_and_404_final():
    calls = []

    def http(url, *a):
        calls.append(1)
        return (504, b"")

    lim = s0d.Limiter(1000.0)
    assert s0d.fetch_one("ipfs://" + CID, LOCAL, http, lim, sleep=lambda s: None)[0] == "http_504"
    assert len(calls) == 3
    calls.clear()

    def h404(url, *a):
        calls.append(1)
        return (404, b"")

    assert s0d.fetch_one("ipfs://" + CID, LOCAL, h404, lim, sleep=lambda s: None)[0] == "http_404"
    assert len(calls) == 1


def test_run_counts_and_no_grp(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "uris.jsonl").write_text(
        json.dumps({"mint": "m1", "uri": "ipfs://" + CID}) + "\n"
        + json.dumps({"mint": "m2", "uri": "https://example.com/a.json"}) + "\n")
    (src / "status.jsonl").write_text(
        json.dumps({"mint": "m1", "block": "b1", "grp": "G", "json_ok": True}) + "\n"
        + json.dumps({"mint": "m2", "block": "b1", "grp": "N", "json_ok": False}) + "\n"
        + json.dumps({"mint": "m3", "block": "b2", "grp": "N", "json_ok": False}) + "\n")
    out = tmp_path / "out"
    s = s0d.run(src, out, LOCAL, lambda *a: (200, b'{"name":"N"}'), 2, 1000.0, 60.0)
    assert s["per_block"]["b1"]["ok"] == 1 and s["per_block"]["b1"]["non_ipfs"] == 1
    assert s["per_block"]["b2"]["no_uri"] == 1
    assert "grp" not in (out / "fetched.jsonl").read_text()
