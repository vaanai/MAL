import json
import os
import stat
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import kalshi_live_log as kl


def test_emit_writes_jsonl_0600(tmp_path):
    log = kl.EventLog(tmp_path / "ev.jsonl", clock=lambda: 12.3456)
    rec = log.emit("refusal", reason="x", price=Decimal("0.4200"))
    assert rec == {"ts": 12.346, "event": "refusal", "reason": "x", "price": "0.4200"}
    log.emit("cancel_all", n=0)
    assert [r["event"] for r in log.read()] == ["refusal", "cancel_all"]
    assert stat.S_IMODE(os.stat(tmp_path / "ev.jsonl").st_mode) == 0o600


def test_sensitive_field_names_always_redacted(tmp_path):
    log = kl.EventLog(tmp_path / "ev.jsonl")
    log.emit("e", signature="abc", key_id="short", headers={"KALSHI-ACCESS-KEY": "k", "Accept": "x"},
             nested=[{"private_key": "p", "ok": 1}])
    txt = (tmp_path / "ev.jsonl").read_text()
    rec = json.loads(txt)
    assert rec["signature"] == kl.REDACTED and rec["key_id"] == kl.REDACTED
    assert rec["headers"] == {"KALSHI-ACCESS-KEY": kl.REDACTED, "Accept": "x"}
    assert rec["nested"] == [{"private_key": kl.REDACTED, "ok": 1}]


def test_registered_values_scrubbed_from_any_string_and_key(tmp_path):
    log = kl.EventLog(tmp_path / "ev.jsonl", forbidden=["KID-1234-5678"])
    sig = "c2lnbmF0dXJl" * 20
    log.add_forbidden(sig)
    log.emit("e", note=f"used KID-1234-5678 and {sig}", **{"k-KID-1234-5678": 1})
    txt = (tmp_path / "ev.jsonl").read_text()
    assert "KID-1234-5678" not in txt and sig not in txt
    assert txt.count(kl.REDACTED) >= 3


def test_pem_block_replaced_and_short_values_ignored(tmp_path):
    log = kl.EventLog(tmp_path / "ev.jsonl", forbidden=["ab"])  # too short to register
    log.emit("e", blob="-----BEGIN PRIVATE KEY-----\nzzz\n-----END PRIVATE KEY-----", t="abab")
    rec = log.read()[0]
    assert rec["blob"] == kl.REDACTED and rec["t"] == "abab"


def test_bytes_not_dumped(tmp_path):
    log = kl.EventLog(tmp_path / "ev.jsonl")
    log.emit("e", body=b"\x00\x01\x02")
    assert log.read()[0]["body"] == "<3 bytes>"
