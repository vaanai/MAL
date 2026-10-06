"""Shared pytest fixtures for tools/ tests."""

from __future__ import annotations

import os

import pytest

# Env vars the V adapter and the EXP-015 screen read. Some code under test sets them in-process
# (e.g. exp012_latency_virtual.set_env); restore them after every test so later files see a clean env.
_ISOLATED_PREFIXES = ("MAL_PSV_", "MAL_EXP015_")


@pytest.fixture(autouse=True)
def _isolate_adapter_env():
    saved = {k: v for k, v in os.environ.items() if k.startswith(_ISOLATED_PREFIXES)}
    yield
    for k in [k for k in os.environ if k.startswith(_ISOLATED_PREFIXES)]:
        if k not in saved:
            del os.environ[k]
    os.environ.update(saved)
