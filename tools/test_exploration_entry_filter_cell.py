"""explore_entry_filter wiring: decision-equivalence of the default B3 grid path,
the single-cell entry point (tools.exploration_entry_model_b3.score_one_cell),
the pool-root allowlist, the hours-actually-opened fence, and the strict
block_time-in-hour filter.

Fixture only (tools/entry_filter_fixture.py): tmp roots of plain .jsonl hours,
no network, no real data, no /data/mal/blocks.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

import tools.exploration_entry_model_b3 as b3
from tools.entry_filter_fixture import build_pools


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


@pytest.fixture(scope="module")
def pools(tmp_path_factory) -> tuple[Path, Path, Path]:
    return build_pools(tmp_path_factory.mktemp("pools"))


# ---------------------------------------------------------------------------
# 1. The default B3 grid path is decision-equivalent to main (golden captured
#    from the UNMODIFIED code at a788cc1, before any edit).
# ---------------------------------------------------------------------------

GOLDEN_ROWS_MD5 = "700978d11ccf58606b91fa37de6202dd"  # run_all_features_a/c/b rows, 3 pools
GOLDEN_N_ROWS = [58, 66, 50]
GOLDEN_JSON_MD5 = "0babab63ec0a19e679add0f77ab460ae"  # b3.main() --out-json bytes (wall_s pinned)
GOLDEN_MD_MD5 = "8eb4a0ef6103869991e0d0939f0d28a2"  # b3.main() --out-md bytes


def default_grid_digests(pools: tuple[Path, Path, Path], tmp: Path) -> dict:
    fast, insample, live = pools
    out_md, out_json = tmp / "o.md", tmp / "o.json"
    argv = ["b3", "--out-md", str(out_md), "--out-json", str(out_json), "--max-workers", "1",
            "--fast-dir", str(fast), "--oracle-insample-dir", str(insample), "--oracle-live-dir", str(live)]
    with mock.patch.object(sys, "argv", argv), mock.patch.object(b3.time, "time", lambda: 1000.0):
        b3.main()
    rows = [
        b3.run_all_features_a(max_workers=1, buffer_hours=2, backfill=fast),
        b3.run_all_features_c(max_workers=1, buffer_hours=2, root=insample),
        b3.run_all_features_b(max_workers=1, buffer_hours=2, root=live),
    ]
    return {
        "rows": _md5(json.dumps(rows, sort_keys=True).encode()),
        "n_rows": [len(r) for r in rows],
        "json": _md5(out_json.read_bytes()),
        "md": _md5(out_md.read_bytes()),
        "report": json.loads(out_json.read_text()),
    }


def test_default_grid_path_is_decision_equivalent_to_pre_change(pools, tmp_path):
    d = default_grid_digests(pools, tmp_path)
    assert d["n_rows"] == GOLDEN_N_ROWS
    assert d["rows"] == GOLDEN_ROWS_MD5
    assert d["json"] == GOLDEN_JSON_MD5
    assert d["md"] == GOLDEN_MD_MD5
    # the golden is not vacuous: models trained and selected trades on real folds
    folds = d["report"]["results"]["tpsl_tp50_sl30"]["s2_clf"]
    assert sum(1 for f in folds.values() if f.get("trained")) >= 5
    assert d["report"]["pooled"]["tpsl_tp50_sl30"]["s2_clf"]["top10"]["n"] > 0
