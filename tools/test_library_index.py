"""Tests for tools/library_index.py and for the committed idea library (docs/library/)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library_index as li  # noqa: E402


def row(**kw) -> dict:
    r = {"id": "x-1", "name": "An idea", "family": "f", "mechanism": "m", "status": "untested", "evidence": "e",
         "why_stopped": "w", "revive_trigger": "r", "next_step": "n", "source_date": "2026-10-13", "section": "library",
         "check": "c"}
    r.update(kw)
    return r


META = {"as_of": "2026-10-13T00:00:00Z", "sources": ["s"], "rulings": ["r"]}


# ---- the committed library ----

def test_committed_rows_are_valid():
    rows = li.load_rows(li.LIB / "ideas.jsonl")
    li.validate(rows)
    assert len(rows) > 100


def test_committed_index_is_what_the_rows_render_to():
    assert li.main(["--check"]) == 0


def test_the_live_row_carries_the_canary_label_and_the_page_carries_the_gate_sentence():
    rows = li.load_rows(li.LIB / "ideas.jsonl")
    live = [r for r in rows if r["status"] == "live-canary" and "duplicate_of" not in r]
    shown = live[0].get("manager_note") or live[0]["why_stopped"]
    assert len(live) == 1 and li.H5_LABEL in shown and li.GATE_SENTENCE in shown
    page = (li.LIB / "INDEX.md").read_text(encoding="utf-8")
    assert li.H5_LABEL in page and "H5 has not cleared the promotion gate" in page


def test_the_page_makes_no_claim_words():
    page = (li.LIB / "INDEX.md").read_text(encoding="utf-8").lower()
    for word in ("profitable", "guaranteed"):
        assert word not in page


# ---- validation ----

def test_a_valid_row_passes():
    li.validate([row()])


@pytest.mark.parametrize("bad", [
    {"status": "winning"}, {"section": "elsewhere"}, {"name": ""}, {"mechanism": 3}, {"extra_key": "x"}, {"status_source": 5},
])
def test_bad_rows_are_refused(bad):
    with pytest.raises(li.LibraryError):
        li.validate([row(**bad)])


def test_a_missing_key_is_refused():
    r = row()
    del r["evidence"]
    with pytest.raises(li.LibraryError):
        li.validate([r])


def test_a_repeated_id_is_refused():
    with pytest.raises(li.LibraryError):
        li.validate([row(), row()])


def test_duplicate_of_must_name_another_row_that_is_not_a_duplicate():
    li.validate([row(id="a"), row(id="b", duplicate_of="a")])
    with pytest.raises(li.LibraryError):
        li.validate([row(id="b", duplicate_of="a")])
    with pytest.raises(li.LibraryError):
        li.validate([row(id="b", duplicate_of="b")])
    with pytest.raises(li.LibraryError):
        li.validate([row(id="a"), row(id="b", duplicate_of="a"), row(id="c", duplicate_of="b")])


LIVE_TEXT = li.H5_LABEL + ". " + li.GATE_SENTENCE + "."


def test_a_live_row_without_the_label_and_gate_sentence_is_refused():
    with pytest.raises(li.LibraryError):
        li.validate([row(status="live-canary")])
    with pytest.raises(li.LibraryError):
        li.validate([row(status="live-canary", why_stopped=li.H5_LABEL)])  # the gate sentence is missing
    li.validate([row(status="live-canary", why_stopped=LIVE_TEXT)])


def test_the_label_is_checked_in_the_text_the_page_shows():
    # the page prints manager_note when a row has one, so the label must be there, not only in why_stopped
    with pytest.raises(li.LibraryError):
        li.validate([row(status="live-canary", why_stopped=LIVE_TEXT, manager_note="a note without the label")])
    li.validate([row(status="live-canary", why_stopped="w", manager_note=LIVE_TEXT)])


def test_a_note_is_never_hidden_in_the_never_tested_and_dead_tables():
    rows = [row(id="a", status="untested", manager_note="jobs ran, no outcome read"),
            row(id="b", status="dead-as-frozen", manager_note="status may overstate")]
    page = li.render(rows, META)
    assert "Note: jobs ran, no outcome read n" in page and "Note: status may overstate w" in page


def test_a_line_that_is_not_json_is_refused(tmp_path):
    p = tmp_path / "ideas.jsonl"
    p.write_text(json.dumps(row()) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(li.LibraryError):
        li.load_rows(p)


# ---- rendering ----

def test_cell_is_one_line_escapes_pipes_and_cuts_at_a_word():
    assert li.cell("a | b\n c") == "a \\| b c"
    cut = li.cell("alpha beta gamma delta", 12)
    assert cut == "alpha beta …" and "\n" not in cut
    assert li.cell("short", 12) == "short"


def test_render_puts_each_row_in_one_place_and_counts_it():
    rows = [row(id="live", status="live-canary", why_stopped="w", manager_note="note today " + LIVE_TEXT),
            row(id="dead", status="dead-as-frozen"), row(id="new", status="untested"),
            row(id="dup", status="untested", duplicate_of="new", duplicate_note="same"),
            row(id="web-thing-1", section="inbox-2026-10-13", status="desk-read"),
            row(id="owner-1", section="owner-ideas", status="untested")]
    li.validate(rows)
    page = li.render(rows, META)
    assert page.count("`live`") == 1 and "note today" in page
    assert page.count("`dup`") == 1 and "| `dup` | An idea | `new` |" in page
    assert "### web-thing (1)" in page
    assert "| **all** | 3 | 1 | 1 |" in page
    assert "The file holds 6 rows: the 5 above and 1 rows kept only as duplicates" in page


def test_render_is_deterministic_and_check_detects_a_stale_page(tmp_path):
    rows = [row()]
    (tmp_path / "ideas.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    (tmp_path / "META.json").write_text(json.dumps(META), encoding="utf-8")
    assert li.render(rows, META) == li.render(rows, META)
    assert li.main(["--dir", str(tmp_path), "--check"]) == 1  # no INDEX.md yet
    assert li.main(["--dir", str(tmp_path)]) == 0
    assert li.main(["--dir", str(tmp_path), "--check"]) == 0
    (tmp_path / "INDEX.md").write_text("edited by hand", encoding="utf-8")
    assert li.main(["--dir", str(tmp_path), "--check"]) == 1


def test_main_returns_2_on_a_bad_library(tmp_path):
    (tmp_path / "ideas.jsonl").write_text(json.dumps(row(status="winning")) + "\n", encoding="utf-8")
    (tmp_path / "META.json").write_text(json.dumps(META), encoding="utf-8")
    assert li.main(["--dir", str(tmp_path)]) == 2


def test_inbox_2026_10_27_is_a_known_section_and_counts_as_inbox():
    assert "inbox-2026-10-27" in li.SECTIONS
    rows = [row(id="p60-x", section="inbox-2026-10-27", status="desk-read"),
            row(id="main-1", status="untested")]
    li.validate(rows)
    page = li.render(rows, META)
    assert "| **all** | 1 | 1 | 0 |" in page
    bad = [row(id="p60-y", section="inbox-2026-10-28")]
    with pytest.raises(li.LibraryError):
        li.validate(bad)
