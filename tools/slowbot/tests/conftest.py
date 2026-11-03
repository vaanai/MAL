import pytest


@pytest.fixture(autouse=True)
def _wallet_flags_in_tmp(tmp_path, monkeypatch):
    """The wallet-wide STOP / HALT constants point at /var/lib/mal-live on the hosts; tests use files under tmp_path."""
    from tools.slowbot import live_executor as lx
    monkeypatch.setattr(lx, "WALLET_STOP", tmp_path / "wallet" / "STOP")
    monkeypatch.setattr(lx, "WALLET_HALT", tmp_path / "wallet" / "HALT")
