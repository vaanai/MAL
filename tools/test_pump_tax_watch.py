"""pump_tax_watch: synthetic account bytes only, no network."""
import hashlib
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("pump_tax_watch", Path(__file__).resolve().parents[1] / "scripts" / "ops" / "pump_tax_watch.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def acct(rate, n=1104):
    b = bytearray(b"\x07" * n)
    if n == 1104:
        b[1096:1104] = rate.to_bytes(8, "little")
    return bytes(b)


def test_rate_zero_exits_0():
    raw = acct(0)
    rec, code = w.evaluate(w.PUMP_PROGRAM, raw, 5)
    assert code == 0 and rec["block0_tax_bps"] == 0 and rec["status"] == "OK_ZERO"
    assert rec["slot"] == 5 and rec["global_len"] == 1104 and rec["global_sha256"] == hashlib.sha256(raw).hexdigest()
    json.dumps(rec)


def test_rate_25_exits_3():
    rec, code = w.evaluate(w.PUMP_PROGRAM, acct(25), 6)
    assert code == 3 and rec["block0_tax_bps"] == 25 and rec["status"] == "NONZERO"


def test_rate_is_little_endian_u64_at_1096():
    rec, _ = w.evaluate(w.PUMP_PROGRAM, acct(0x0102030405060708), 1)
    assert rec["block0_tax_bps"] == 0x0102030405060708


def test_wrong_length_exits_2():
    for n in (1103, 1105, 0):
        rec, code = w.evaluate(w.PUMP_PROGRAM, acct(0, n), 1)
        assert code == 2 and rec["block0_tax_bps"] is None and rec["status"] == "ERROR_LEN"


def test_wrong_owner_exits_2():
    rec, code = w.evaluate("11111111111111111111111111111111", acct(25), 1)
    assert code == 2 and rec["block0_tax_bps"] is None and rec["status"] == "ERROR_OWNER"


def test_missing_account_exits_2():
    rec, code = w.evaluate(None, None, 1)
    assert code == 2


def test_main_read_error_is_one_json_line_exit_2(monkeypatch, capsys):
    def boom(url):
        raise OSError("down")
    monkeypatch.setattr(w, "fetch", boom)
    monkeypatch.setenv("PUMP_TAX_RPC", "https://example.invalid/key-should-not-print")
    assert w.main() == 2
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1 and "key-should" not in out
    assert json.loads(out)["status"].startswith("ERROR_READ")


def test_main_ok(monkeypatch, capsys):
    monkeypatch.setattr(w, "fetch", lambda url: (w.PUMP_PROGRAM, acct(0), 9))
    assert w.main() == 0
    assert json.loads(capsys.readouterr().out)["block0_tax_bps"] == 0
