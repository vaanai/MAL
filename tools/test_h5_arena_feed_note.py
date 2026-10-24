"""The FEED_NOTE_MERGED flip (QP-SCOPE-1022-h5exec item 17; EXP-024 Note under Amendment 10, #851). Fixtures only: no network, no key, nothing sent.

What the flip changes, and what it must not: the covered book (Q50) may read a live feed (dry) and passes the feed-note check of a live start; the ended or
undeclared books stay replay-only and are refused a live start; a live start still needs every Helm-owned file (both LIVE_OK, TIER T1), the canary DEC line
and the key drop-in, so the flip alone never lets a send happen."""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from tools import h5_arena_executor as ax
from tools.test_h5_arena_executor import A2, A2V2, Q50, ACase, write_canary_record

REPO = Path(__file__).resolve().parent.parent


def test_the_flip_and_the_declared_books():
    assert ax.FEED_NOTE_MERGED is True
    assert ax.FEED_NOTE_BOOKS == frozenset({Q50})
    assert Q50 in ax.BOOKS


def test_the_note_is_in_the_tree_and_names_exactly_the_covered_books():
    text = (REPO / "EXP" / "EXP-024-h5-boostfloor-part1-prereg.md").read_text()
    assert ax.FEED_NOTE_REF in text
    note = text[text.index(ax.FEED_NOTE_REF):text.index("## Sources")]
    assert f"One `bot_id`: `{Q50}`" in note
    for ended in (A2, A2V2):
        assert f"`{ended}`" in note and "ENDED NOT_DECIDABLE" in note  # named only as not covered
    assert "frozen_sha` `a5f413704eb120c8f529101a6186e3a95de7f83e`" in note and ax.BOOKS[Q50].frozen_sha == "a5f413704eb120c8f529101a6186e3a95de7f83e"
    assert "`clock_start_ms` 1792886400000" in note and ax.BOOKS[Q50].clock_start_ms == 1792886400000


class RealSetTests(ACase):
    """With the REAL FEED_NOTE_BOOKS (the ACase fixture widens it for the older tests)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(ax, "FEED_NOTE_BOOKS", frozenset({Q50}))
        p.start()
        self.addCleanup(p.stop)
        write_canary_record(self.tmp / "repo")

    def live_cfg(self, book):
        return {"book_id": book, "mode": "live", "a3_record_file": "/x", "intents_file": "/x", "end_ms": 1, "funded_lamports": 500_000_000}

    def lok(self, book):
        ax.ARENA_LIVE_OK_PATH.write_text(book + "\n")
        os.chmod(ax.ARENA_LIVE_OK_PATH, 0o644)

    def test_dry_live_feed_only_for_the_covered_book(self):
        root = self.tmp / "repo"
        self.assertIsNone(ax.arena_start_refusal(ax.BOOKS[Q50], {"book_id": Q50}, root, live=False))
        for b in (A2, A2V2):
            self.assertEqual(ax.arena_start_refusal(ax.BOOKS[b], {"book_id": b}, root, live=False), "feed_note_not_covering_book_replay_only")
            with mock.patch.object(ax, "v2_start_refusal", lambda: None):  # (the v2 rule-source file check is tested elsewhere)
                self.assertIsNone(ax.arena_start_refusal(ax.BOOKS[b], {"book_id": b, "replay_only": True}, root, live=False))  # replay stays allowed

    def test_live_start_of_an_ended_book_is_refused(self):
        for b in (A2, A2V2):
            self.lok(b)
            self.assertEqual(ax.live_entry_refusal(ax.BOOKS[b], self.live_cfg(b)), "live_feed_note_not_covering_book")

    def test_the_flip_alone_never_lets_a_send_happen(self):
        """Q50 passes the feed-note check, and every Helm-owned file is still required: no arena LIVE_OK, the wrong book in it, no shared LIVE_OK,
        a T0 tier, or no canary line each refuse the live start."""
        root = self.tmp / "repo"
        c = {**self.live_cfg(Q50), "exit_gap_file": str(self.tmp / "state" / "arena-parity" / "exit-gaps.jsonl"), "state_dir": str(self.tmp / "state"),
             "parity_twins_file": "/x/t", "tieout_dir": "/x/d"}
        self.assertEqual(ax.live_entry_refusal(ax.BOOKS[Q50], c), "arena_live_ok_missing")
        self.lok(A2)
        self.assertEqual(ax.live_entry_refusal(ax.BOOKS[Q50], c), "arena_live_ok_other_book")
        self.lok(Q50)
        self.assertIsNone(ax.live_entry_refusal(ax.BOOKS[Q50], c))
        self.assertEqual(ax.arena_start_refusal(ax.BOOKS[Q50], c, root, live=True), "live_ok_missing")  # the shared /etc/mal-h5/LIVE_OK
        ax.h.LIVE_OK_PATH.write_text("")
        os.chmod(ax.h.LIVE_OK_PATH, 0o644)
        self.arena_tier.write_text("T0\n")
        self.assertEqual(ax.arena_start_refusal(ax.BOOKS[Q50], c, root, live=True), "arena_tier_not_t1:T0")
        self.arena_tier.write_text("T1\n")
        (root / "DEC" / "DEC-033-h5-arena-canary-test.md").write_text(f"OWNER_CANARY_CONFIRMED: bot_id={A2}\n")
        self.assertEqual(ax.arena_start_refusal(ax.BOOKS[Q50], c, root, live=True), "canary_record_for_bot_id_missing")
        write_canary_record(root, Q50)
        self.assertIsNone(ax.arena_start_refusal(ax.BOOKS[Q50], c, root, live=True))  # keyless checks pass; the key itself comes only from the drop-in credential
