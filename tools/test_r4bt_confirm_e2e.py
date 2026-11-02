"""r4bt confirmation, END TO END (QP C1 on #1198): the REAL counter (`r4bt_attempts.count --from-ms`), the REAL pricer (`r4bt_price.price`) and the REAL read wrapper
(`r4bt_read_confirm.confirm` over `r4bt_read.read`), no stub of any of them. Synthetic only: a synthetic declog and tape in a tmp dir, a synthetic registry, health table and
screen record. It shows that nothing decided before the screen read's instant is priced, and that the read's own consistency checks (boundary and attempt-count equality against the
counter) pass on the confirmation ledger."""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from tools import arena_score as S
from tools import r4bt_attempts as AT
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools import r4bt_read_confirm as CF
from tools.arena_paper import Ledger
from tools.test_r4bt_price import CLOCK, DAY, HR, NOW, entry_body, exit_body, world
from tools.test_r4bt_read import rkw

SCREEN_AT = (CLOCK // DAY + 1) * DAY  # the screen read's 00:00Z instant (accepted by QP as the screen read's instant)
CONF_B = SCREEN_AT + DAY  # the first 00:00Z with the (patched) minimum of confirmation attempts


@pytest.fixture(autouse=True)
def _small_book(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
    monkeypatch.setattr(S, "MIN_N", 3)  # the synthetic book has 3 confirmation attempts, not 100: the SAME constants feed the counter and the scorer
    monkeypatch.setattr(S, "MIN_DAYS", 1)
    monkeypatch.setattr(AT, "GATE_ATTEMPTS", 3)
    monkeypatch.setattr(AT, "GATE_DATES", 1)


def decisions():
    """Orders 1-3 are decided on 11-07 (BEFORE the screen read's instant), orders 4-6 on 11-08 (after it)."""
    d = []
    for base, first in ((CLOCK, 1), (SCREEN_AT, 4)):
        for i in range(3):
            d.append((base + i * HR + 1200, entry_body(order=f"MP1:{first + i}")))
            if i:
                d.append((base + i * HR + 1200, exit_body(order=f"MP1:{first + i - 1}")))
        d.append((base + 3 * HR + 1200, exit_body(order=f"MP1:{first + 2}")))
    return d


def test_the_confirmation_count_prices_only_attempts_after_the_screen_instant_and_the_read_checks_pass(tmp_path):
    kw = world(tmp_path, decisions=decisions(), tape_kw={"hours": 34})
    # 1. the REAL counter, confirmation mode, on the declog the book wrote
    tbl = tmp_path / "hours.json"
    tbl.write_text(json.dumps({"tabled_from": "2026-11-08T00", "tabled_through": "2026-11-08T23", "excluded_hours": []}))
    cnt = AT.count(Path(kw["declog_root"]), G.ARM, CLOCK, 14, CONF_B + HR, AT.load_hour_table(tbl), from_ms=SCREEN_AT)
    assert cnt["mode"] == "confirmation" and cnt["status"] == "boundary" and cnt["boundary_ms"] == CONF_B and cnt["attempts_before_boundary"] == 3
    assert cnt["not_counted_before_from"] == 3 and cnt["chain_verified"] is True
    bnd = tmp_path / "confirm-boundary.json"
    bnd.write_text(json.dumps(cnt, sort_keys=True))
    kw.update(boundary_file=bnd, boundary_sha256=G.sha256_file(bnd))
    # 2. the REAL pricer into a FRESH root: the three attempts decided before the screen instant are not selected, so they are never priced
    ids = tmp_path / "ids.json"
    res = P.price(**dict(kw, v_audit_ids_out=ids))
    assert res["from_ms"] == SCREEN_AT and res["entries_selected"] == 3 and res["entries_not_selected"] == {"before_from_ms": 3}
    rows = Ledger(tmp_path / "ledger").read_bot(G.BOT_ID)
    text = json.dumps(rows, sort_keys=True)
    assert rows and all(f"MP1:{n}" not in text for n in (1, 2, 3)) and all(f"MP1:{n}" in text for n in (4, 5, 6))
    rep = tmp_path / "report.json"
    rep.write_text(json.dumps(res, sort_keys=True))
    kw.update(report=rep, ids=ids)
    # 3. the REAL read wrapper through the confirmation: its boundary equality and attempt-count equality against the counter pass, and the confirmation rule is applied
    screen = tmp_path / "screen" / f"{G.BOT_ID}.json"
    screen.parent.mkdir()
    screen.write_text(json.dumps({"bot_id": G.BOT_ID, "status": "PASS", "read_boundary_ms": SCREEN_AT}))
    rk = rkw(tmp_path, kw, as_of_ms=CONF_B + HR, c=1)
    lines, out = CF.confirm(screen_record=str(screen), screen_record_sha256=G.sha256_file(screen), confirm_reads_dir=str(tmp_path / "confirm"), record=True, **rk)
    assert out["read_boundary_ms"] == CONF_B and out["attempts"] == 3  # equal to the counter's boundary_ms and attempts_before_boundary (the read refuses otherwise)
    assert out["status"] in ("PASS", "FAIL") and out["confirmation"]["c"] == 1 and out["confirmation"]["alpha_over_c"] == 0.05
    assert any("CONFIRMATION (DEC-031 section 2 step 4)" in ln for ln in lines) and any(ln.startswith("recorded: ") for ln in lines)
    assert json.loads((tmp_path / "confirm" / f"{G.BOT_ID}.json").read_text())["confirmation"]["screen_record_sha256"] == G.sha256_file(screen)
