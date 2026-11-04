"""Rule B as amended (QP-RULING-1104-gradscore-hist): uri_class strata, content-addressed gate, coverage, pins, input guard. Synthetic only."""
import pytest

from tools.gradscore import hist_read as hr
from tools.gradscore.tests.test_hist_read import write_block


def test_uri_class():
    from tools.gradscore.hist import uri_class
    assert uri_class("https://ipfs.io/ipfs/bafy1") == "content_addressed" and uri_class("ipfs://bafy") == "content_addressed"
    assert uri_class("https://arweave.net/abc") == "content_addressed" and uri_class("https://gateway.irys.xyz/abc") == "content_addressed"
    assert uri_class("https://host.example/m.json") == "http" and uri_class(None) == "none" and uri_class("") == "none"


def test_pass_requires_content_addressed_stratum(tmp_path):
    for b in hr.PERIODS["september_pooled"] + hr.PERIODS["explore-0814"]:
        write_block(tmp_path, b, 600, True, ca_signal=False)  # signal only on the http rows
    per = {p: hr.load_period(str(tmp_path), bl)[0] for p, bl in hr.PERIODS.items()}
    res = hr.read_periods(per, draws=1000)
    assert res["pass_content_addressed"]["A:runner_prob_pct"] is False and res["pass_both"]["A:runner_prob_pct"] is False
    assert set(res["strata"]["explore-0814"]) >= {"uri_class=content_addressed", "uri_class=http", "uri_class=none", "has_metadata=True"}


def test_refuses_unenriched_cards(tmp_path):
    for b in hr.PERIODS["september_pooled"] + hr.PERIODS["explore-0814"]:
        write_block(tmp_path, b, 50, True, with_uri=False)
    with pytest.raises(hr.Refused):
        hr.load_period(str(tmp_path), hr.PERIODS["explore-0814"])
    assert hr.main(["--dir", str(tmp_path), "--allow-outcomes"]) == 2
    (tmp_path / f"enrich-{hr.PERIODS['explore-0814'][0]}.jsonl").unlink()
    with pytest.raises(hr.Refused):
        hr.load_period(str(tmp_path), hr.PERIODS["explore-0814"])


def test_coverage_and_pins(tmp_path):
    write_block(tmp_path, "explore-0814", 100, True, skip_every=10)
    rows, exc = hr.load_period(str(tmp_path), ("explore-0814",))
    assert exc["coverage"]["A"] == {"cards": 100, "scored": 90, "parse_skip": 10, "never_scored": 0}
    assert exc["coverage"]["B"] == {"cards": 100, "scored": 90, "parse_skip": 0, "never_scored": 10}
    assert exc["cap_hit"] == {"explore-0814": False}
    assert len(exc["pins"]) == 5 and all(len(v) == 64 for v in exc["pins"].values()) and any(k.endswith("auc.py") for k in exc["pins"])
