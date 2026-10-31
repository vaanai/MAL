"""E1 restart drill (QP-RULING-1026-entry-rate open item 1): the drill passes on synthetic tape and uses no API. Roots are patched to a temp dir; no /data/mal path."""

from __future__ import annotations

import json
import os

from tools import drill_hv6_e1_halt as E1
from tools import drill_hv6_item6 as D6
from tools import hv6_cards as H
from tools import hv6_review as RR


def test_e1_halt_drill_passes_on_synthetic_tape(tmp_path, monkeypatch, capsys):
    root = os.path.realpath(tmp_path / "haiku-v6")
    os.makedirs(root)
    for mod in (H, RR, D6):
        if hasattr(mod, "HV6_ROOT"):
            monkeypatch.setattr(mod, "HV6_ROOT", root)
    monkeypatch.setattr(H, "WRITE_ROOTS", (root,))
    monkeypatch.setattr(H, "READ_ROOTS", (*H.READ_ROOTS, root))
    assert E1.main(["--drill-id", "t-e1", "--sha", "0" * 40]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    r = next(x for x in lines if x.get("drill") == "e1_halt")
    assert r["result"] == "PASS" and r["api_calls"] == 0
    assert r["A"]["ref_entries_at_or_after_halt"] > 0 and r["A"]["entries_after_halt_total"] == 0   # not vacuous: the reference would have entered
    assert r["B"]["ref_x_entries_before_halt"] > 0 and r["B"]["arms_equal_ref_before_halt"] and r["B"]["entries_after_halt_total"] == 0
