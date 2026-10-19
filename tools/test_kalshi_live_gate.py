import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

import kalshi_live_gate as kg


def _setup(tmp_path, *, armed_line=None, mode_dir=0o700, mode_env=0o600, mode_pem=0o600):
    kd = tmp_path / "kd"
    kd.mkdir()
    (kd / "kalshi.env").write_text(
        "KALSHI_KEY_ID=kid-for-test-0001\nKALSHI_BASE_URL=https://offline.invalid/trade-api/v2/\nKALSHI_ENV=demo\n")
    (kd / "kalshi-private.pem").write_text("not-a-real-key\n")  # the gate never reads the key
    decl = tmp_path / "DECLARE-LIVE-1.md"
    decl.write_text("trial pre-registration, frozen\n")
    sha = hashlib.sha256(decl.read_bytes()).hexdigest()
    armed = tmp_path / "LIVE-ARMED"
    armed.write_text(armed_line if armed_line is not None else f"DECLARE-LIVE-SHA256 {sha}\n")
    os.chmod(armed, 0o644)  # do not depend on the process umask
    os.chmod(kd / "kalshi.env", mode_env)
    os.chmod(kd / "kalshi-private.pem", mode_pem)
    os.chmod(kd, mode_dir)
    return kd, decl, armed, sha


def _code(**kw):
    with pytest.raises(kg.Refused) as ei:
        kg.arm(**kw)
    return ei.value.code


def test_all_three_conditions_arm(tmp_path):
    kd, decl, armed, sha = _setup(tmp_path)
    ctx = kg.arm(True, decl, armed, kd)
    assert ctx.key_id == "kid-for-test-0001" and ctx.env == "demo"
    assert ctx.base_url == "https://offline.invalid/trade-api/v2"
    assert ctx.declaration_sha256 == sha and ctx.key_path == kd / "kalshi-private.pem"
    assert "kid-for-test-0001" not in repr(ctx)  # key id kept out of repr


def test_bare_hex_line_and_comments_accepted(tmp_path):
    kd, decl, armed, sha = _setup(tmp_path)
    armed.write_text(f"# armed by owner\n{sha}\n")  # rewrite keeps the 0644 mode
    assert kg.arm(True, decl, armed, kd).declaration_sha256 == sha


def test_refuses_without_live_flag(tmp_path):
    kd, decl, armed, _ = _setup(tmp_path)
    assert _code(live=False, declaration=decl, armed_file=armed, key_dir=kd) == "no_live_flag"
    assert _code(live=None, declaration=decl, armed_file=armed, key_dir=kd) == "no_live_flag"
    assert _code(live="yes", declaration=decl, armed_file=armed, key_dir=kd) == "no_live_flag"


def test_refuses_sha_mismatch_when_declaration_changes(tmp_path):
    kd, decl, armed, _ = _setup(tmp_path)
    decl.write_text("edited after arming\n")
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "sha_mismatch"


def test_refuses_wrong_hash_in_armed(tmp_path):
    kd, decl, armed, _ = _setup(tmp_path, armed_line="DECLARE-LIVE-SHA256 " + "0" * 64 + "\n")
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "sha_mismatch"


def test_refuses_missing_or_malformed_armed(tmp_path):
    kd, decl, armed, sha = _setup(tmp_path)
    for body in ["", "garbage\n", f"{sha}\n{sha}\n", sha[:-1] + "\n", sha.upper() + "\n"]:
        armed.write_text(body)
        assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "armed_malformed", body
    armed.unlink()
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "no_armed_file"


def test_refuses_writable_armed_and_symlink_armed(tmp_path):
    kd, decl, armed, sha = _setup(tmp_path)
    os.chmod(armed, 0o666)
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "armed_perms"
    os.chmod(armed, 0o644)
    link = tmp_path / "LIVE-ARMED-link"
    link.symlink_to(armed)
    assert _code(live=True, declaration=decl, armed_file=link, key_dir=kd) == "armed_not_regular"


def test_refuses_bad_declaration(tmp_path):
    kd, decl, armed, _ = _setup(tmp_path)
    assert _code(live=True, declaration=None, armed_file=armed, key_dir=kd) == "no_declaration"
    assert _code(live=True, declaration=tmp_path / "DECLARE-LIVE-9.md", armed_file=armed, key_dir=kd) == "no_declaration"
    other = tmp_path / "notes.md"
    other.write_text("x")
    assert _code(live=True, declaration=other, armed_file=armed, key_dir=kd) == "bad_declaration_name"
    empty = tmp_path / "DECLARE-LIVE-0.md"
    empty.write_text("")
    assert _code(live=True, declaration=empty, armed_file=armed, key_dir=kd) == "declaration_empty"


@pytest.mark.parametrize("which,mode", [("env", 0o644), ("env", 0o640), ("pem", 0o644), ("pem", 0o660), ("pem", 0o400)])
def test_refuses_key_files_not_0600(tmp_path, which, mode):
    kd, decl, armed, _ = _setup(tmp_path)
    os.chmod(kd / ("kalshi.env" if which == "env" else "kalshi-private.pem"), mode)
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "key_file_perms"


def test_refuses_key_dir_open_missing_or_files_missing(tmp_path):
    kd, decl, armed, _ = _setup(tmp_path)
    os.chmod(kd, 0o750)
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "key_dir_perms"
    os.chmod(kd, 0o700)
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=tmp_path / "nope") == "key_dir_missing"
    (kd / "kalshi-private.pem").unlink()
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "key_file_missing"


def test_refuses_symlinked_key_file(tmp_path):
    kd, decl, armed, _ = _setup(tmp_path)
    real = tmp_path / "real.pem"
    real.write_text("x")
    os.chmod(real, 0o600)
    (kd / "kalshi-private.pem").unlink()
    (kd / "kalshi-private.pem").symlink_to(real)
    assert _code(live=True, declaration=decl, armed_file=armed, key_dir=kd) == "key_file_missing"


@pytest.mark.parametrize("env_text", [
    "KALSHI_BASE_URL=https://x.invalid/trade-api/v2\nKALSHI_ENV=demo\n",          # no key id
    "KALSHI_KEY_ID=k1234567\nKALSHI_BASE_URL=https://x.invalid/v2\nKALSHI_ENV=staging\n",
    "KALSHI_KEY_ID=k1234567\nKALSHI_BASE_URL=http://x.invalid/v2\nKALSHI_ENV=demo\n",
    "KALSHI_KEY_ID=k1234567\nKALSHI_BASE_URL=https://u:p@x.invalid/v2\nKALSHI_ENV=demo\n",
    "KALSHI_KEY_ID=k1234567\nKALSHI_ENV=demo\n",
])
def test_refuses_bad_env_and_error_text_has_no_values(tmp_path, env_text):
    kd, decl, armed, _ = _setup(tmp_path)
    (kd / "kalshi.env").write_text(env_text)
    with pytest.raises(kg.Refused) as ei:
        kg.arm(True, decl, armed, kd)
    assert ei.value.code == "env_bad"
    assert "k1234567" not in str(ei.value) and "u:p" not in str(ei.value)


def test_default_armed_location_is_inside_key_dir(tmp_path):
    kd, decl, armed, _ = _setup(tmp_path)
    armed.rename(kd / "LIVE-ARMED")
    os.chmod(kd / "LIVE-ARMED", 0o644)
    assert kg.arm(True, decl, None, kd).env == "demo"
