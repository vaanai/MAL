"""The rotation calibration DRAFTS (docs/rotation/books/*.calibration-*.draft.json) are refused by the decay monitor as they stand (TODO-QP tokens are strings), and load once every
token is replaced by a valid number, so the draft's keys match the monitor's schema (rotation-decay-calibration.v1). The placeholder numbers below are NOT calibration values."""

import json
import re
from pathlib import Path

import pytest

from tools import rotation_decay_monitor as M

ROOT = Path(__file__).resolve().parents[1]
DRAFTS = sorted((ROOT / "docs/rotation/books").glob("*.calibration-*.draft.json"))
TOKEN = re.compile(r"^TODO-QP-[A-Z0-9.-]+$")
FILL = {"winsor_lo": -1.0, "winsor_hi": 3.0, "mu0": 0.01, "mu1": -0.05, "h_w": 1.0, "h_a": 2.0, "sim_source_sha256": "0" * 64}


def _bot(p: Path) -> str:
    return p.name.split(".calibration-")[0]


def test_drafts_exist():
    assert {_bot(p) for p in DRAFTS} >= {"rot-ce1-v1", "rot-e1-v1"}


@pytest.mark.parametrize("path", DRAFTS, ids=lambda p: p.name)
def test_draft_refused_as_is(path):
    with pytest.raises(M.MonitorError):
        M.load_calibration(path, _bot(path))


@pytest.mark.parametrize("path", DRAFTS, ids=lambda p: p.name)
def test_draft_loads_when_tokens_filled(path, tmp_path):
    raw = json.loads(path.read_text())
    assert raw["schema"] == M.SCHEMA and raw["bot_id"] == _bot(path)
    assert path.name == f"{raw['bot_id']}.calibration-{raw['stake_lamports']}.draft.json"
    tokens = [v for v in raw.values() if isinstance(v, str) and TOKEN.match(v)] + [v for v in raw["mu0_by_rung"].values() if isinstance(v, str)]
    assert tokens and all(TOKEN.match(t) for t in tokens)
    for k, v in FILL.items():
        assert isinstance(raw[k], str) and TOKEN.match(raw[k]), k
        raw[k] = v
    raw["mu0_by_rung"] = {r: (FILL["mu0"] if int(r) == raw["stake_lamports"] else 0.005) for r in raw["mu0_by_rung"]}
    out = tmp_path / path.name
    out.write_text(json.dumps(raw))
    cal = M.load_calibration(out, raw["bot_id"])
    assert cal["stake_lamports"] in M.RUNGS and cal["n0"] == 25
    for tgt in {t for t in M.LADDER[cal["stake_lamports"]] if t is not None}:
        assert tgt in cal["mu0_by_rung"]
