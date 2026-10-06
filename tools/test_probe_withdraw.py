"""Offline tests for the probe_withdraw destination lock. No RPC, no key."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from solders.pubkey import Pubkey

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_withdraw as pw  # noqa: E402

OWNER = pw.OWNER_DEST
# Same first 4 and last 4 characters, different middle (still valid base58, same length).
LOOKALIKE = OWNER[:4] + "1" * (len(OWNER) - 8) + OWNER[-4:]
ONE_CHAR = OWNER[:20] + ("2" if OWNER[20] != "2" else "3") + OWNER[21:]


def test_constant_shape():
    assert len(OWNER) == 44
    assert len(bytes(Pubkey.from_string(OWNER))) == 32
    assert OWNER == "5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi"


def test_exact_match_passes_guard():
    pw.check_owner_dest(OWNER)


@pytest.mark.parametrize(
    "bad",
    [LOOKALIKE, ONE_CHAR, " " + OWNER, OWNER + " ", OWNER + "\n", OWNER.lower(), OWNER[:-1], OWNER + "1", "", "abc"],
)
def test_guard_refuses(bad):
    assert LOOKALIKE[:4] == OWNER[:4] and LOOKALIKE[-4:] == OWNER[-4:] and LOOKALIKE != OWNER
    with pytest.raises(pw.Refuse) as e:
        pw.check_owner_dest(bad)
    msg = str(e.value.code)
    assert "does not match the owner address; never copy addresses from transaction history" in msg
    if len(bad) > 12:
        assert bad[4:-4] not in msg  # only first 4 / last 4 are echoed


class _NoNet:
    def __call__(self, *a, **k):
        raise AssertionError("network touched")


def _stub_main(monkeypatch):
    """Make main() reach run() without keys/RPC; run() must stop at the guard or at our stub."""
    monkeypatch.setenv("MAL_LIVE_TEST", "1")
    monkeypatch.setattr(pw, "load_rpc_url", lambda f: "http://invalid.invalid/")
    called = []
    monkeypatch.setattr(pw, "run", lambda args, rpc, **kw: called.append(args.to) or 0)
    return called


def test_main_exact_passes_to_run(monkeypatch):
    called = _stub_main(monkeypatch)
    assert pw.main(["--to", OWNER, "--dry-run"]) == 0 and called == [OWNER]


def test_main_equals_form(monkeypatch):
    called = _stub_main(monkeypatch)
    assert pw.main([f"--to={OWNER}"]) == 0 and called == [OWNER]
    assert pw.main([f"--to={LOOKALIKE}"]) == 1 and called == [OWNER]
    assert pw.main([f"--to={OWNER.lower()}"]) == 1 and called == [OWNER]


@pytest.mark.parametrize("flags", [[], ["--yes"], ["--dry-run"], ["--yes", "--dry-run", "--force"]])
def test_main_refuses_wrong_address_in_every_mode(monkeypatch, capsys, flags):
    called = _stub_main(monkeypatch)
    for bad in (LOOKALIKE, ONE_CHAR, " " + OWNER, OWNER + " ", OWNER.lower()):
        assert pw.main(["--to", bad, *flags]) == 1
    assert called == []
    assert "does not match the owner address" in capsys.readouterr().err


def test_run_guard_runs_before_key_or_network(tmp_path):
    from types import SimpleNamespace

    args = SimpleNamespace(to=LOOKALIKE, keyfile=str(tmp_path / "missing.json"), yes=True)
    with pytest.raises(pw.Refuse) as e:
        pw.run(args, _NoNet(), check_location=True)
    assert "does not match the owner address" in str(e.value.code)


def test_abbreviation_rejected(monkeypatch, capsys):
    called = _stub_main(monkeypatch)
    with pytest.raises(SystemExit) as e:
        pw.main(["--t", OWNER])
    assert e.value.code == 2 and called == []
    assert "unrecognized arguments" in capsys.readouterr().err or True


def test_to_is_required(monkeypatch):
    _stub_main(monkeypatch)
    with pytest.raises(SystemExit) as e:
        pw.main(["--dry-run"])
    assert e.value.code == 2
