import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

pytest.importorskip("cryptography")
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding

import kalshi_live_sign as ks
import kalshi_live_testutil as tu


@pytest.fixture(scope="module")
def key():
    return tu.make_rsa_key()


def _verify_independently(pub, msg: bytes, sig_b64: str):
    """Documented scheme re-stated in the test, not via the module under test."""
    pub.verify(base64.b64decode(sig_b64), msg,
               padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
               hashes.SHA256())


def test_message_known_answer_strips_query():
    assert ks.signing_message(1700000000123, "post", "/trade-api/v2/portfolio/events/orders") == \
        b"1700000000123POST/trade-api/v2/portfolio/events/orders"
    assert ks.signing_message(5, "GET", "/trade-api/v2/portfolio/fills?limit=5&cursor=x") == \
        b"5GET/trade-api/v2/portfolio/fills"


def test_message_rejects_bad_inputs():
    for args in [(0, "GET", "/a"), (True, "GET", "/a"), ("1", "GET", "/a"), (1, "PATCH", "/a"), (1, "GET", "a")]:
        with pytest.raises(ks.SignError):
            ks.signing_message(*args)


def test_signature_verifies_with_public_key_known_answer(key):
    ts, method, path = 1700000000123, "POST", "/trade-api/v2/portfolio/events/orders"
    h = ks.auth_headers("kid", key, method, path + "?x=1", ts)
    assert set(h) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"}
    assert h["KALSHI-ACCESS-KEY"] == "kid" and h["KALSHI-ACCESS-TIMESTAMP"] == str(ts)
    assert len(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"])) == 256  # 2048-bit RSA
    msg = f"{ts}{method}{path}".encode()
    _verify_independently(key.public_key(), msg, h["KALSHI-ACCESS-SIGNATURE"])  # raises on failure
    assert ks.verify_message(key.public_key(), msg, h["KALSHI-ACCESS-SIGNATURE"])


def test_signature_binds_timestamp_method_and_path(key):
    ts, path = 1700000000123, "/trade-api/v2/portfolio/balance"
    sig = ks.auth_headers("kid", key, "GET", path, ts)["KALSHI-ACCESS-SIGNATURE"]
    pub = key.public_key()
    for ts2, m2, p2 in [(ts + 1, "GET", path), (ts, "DELETE", path), (ts, "GET", path + "x")]:
        assert not ks.verify_message(pub, ks.signing_message(ts2, m2, p2), sig)
        with pytest.raises(InvalidSignature):
            _verify_independently(pub, ks.signing_message(ts2, m2, p2), sig)


def test_pss_is_randomised_and_both_verify(key):
    m = ks.signing_message(9, "GET", "/p")
    a, b = ks.sign_message(key, m), ks.sign_message(key, m)
    assert a != b
    assert ks.verify_message(key.public_key(), m, a) and ks.verify_message(key.public_key(), m, b)


def test_verify_rejects_wrong_key_and_garbage(key):
    other = tu.make_rsa_key()
    m = ks.signing_message(9, "GET", "/p")
    sig = ks.sign_message(key, m)
    assert not ks.verify_message(other.public_key(), m, sig)
    assert not ks.verify_message(key.public_key(), m, "not base64 !!")
    assert not ks.verify_message(key.public_key(), m, base64.b64encode(b"x" * 256).decode())


def test_load_private_key_roundtrip_and_perm_checks(tmp_path, key):
    d, _ = tu.make_key_dir(tmp_path / "kd", key=key)
    loaded = ks.load_private_key(d / "kalshi-private.pem")
    m = ks.signing_message(9, "GET", "/p")
    assert ks.verify_message(key.public_key(), m, ks.sign_message(loaded, m))
    os.chmod(d / "kalshi-private.pem", 0o640)
    with pytest.raises(ks.SignError, match="0600"):
        ks.load_private_key(d / "kalshi-private.pem")
    os.chmod(d / "kalshi-private.pem", 0o600)
    link = tmp_path / "link.pem"
    link.symlink_to(d / "kalshi-private.pem")
    with pytest.raises(ks.SignError, match="regular"):
        ks.load_private_key(link)


def test_load_private_key_rejects_non_rsa_garbage_and_small(tmp_path):
    from cryptography.hazmat.primitives import serialization
    p = tmp_path / "k.pem"
    ec_key = ec.generate_private_key(ec.SECP256R1())
    p.write_bytes(ec_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    os.chmod(p, 0o600)
    with pytest.raises(ks.SignError, match="RSA"):
        ks.load_private_key(p)
    p.write_text("garbage")
    with pytest.raises(ks.SignError, match="PEM"):
        ks.load_private_key(p)
    p.write_bytes(tu.pem_bytes(tu.make_rsa_key(1024)))
    with pytest.raises(ks.SignError, match="2048"):
        ks.load_private_key(p)


def test_error_text_carries_no_pem_content(tmp_path):
    p = tmp_path / "k.pem"
    p.write_text("-----BEGIN PRIVATE KEY-----\nQUJDRA==\n-----END PRIVATE KEY-----\n")
    os.chmod(p, 0o600)
    with pytest.raises(ks.SignError) as ei:
        ks.load_private_key(p)
    assert "QUJDRA" not in str(ei.value) and ei.value.__cause__ is None
