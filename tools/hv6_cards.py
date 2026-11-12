#!/usr/bin/env python3
"""hv6_cards: the code half of the HV6 scanner (persona "GEN") over the UNMODIFIED tools/hv4_cards.py and tools/hv4_chain.py.

Serves docs/research/hv6/DESIGN.md (branch claude/hv5-design, PR #904) section 2 "Universe, scanner, cadence", section 8 (seals and guard) and
section 10 build item 1. PAPER research only: pure python, no network, no keys, no model call, no tape read. Nothing here trades. Nothing here says a
book makes money.

What this file is: HV4's lane G universe (filters, features, setup predicates, hard vetoes, card) is imported, never copied or edited; HV6 adds only
  * the GEN persona: CON and BIG merged, ONE setup per pool per 30 min (PULLBACK, else BREAKOUT, else SECOND_WAVE), cooldown keyed "GEN";
  * the 2 h age guard (section 8.5): a hard code check that refuses any card under 2 h, independent of the HV4 `age_min` filter;
  * the board (BOARD_SIZE = 4 pools every 15 min at :02 by 60-min volume; 24 before fix B, see BOARD_SIZE) and X's 2/h cap;
  * the HV6 code limits as data (design table, section 2);
  * the path allowlist guard (section 8.2) and an import guard (section 8.2, "an import guard blocks modules that read outputs").

HV4 code is reached only through `import tools.hv4_cards` / `import tools.hv4_chain`. The blob-sha test in tools/test_hv6_cards.py asserts both files equal
HV4's frozen sha 9ed4c5ff63935696b2a89150e13565e123047754 (the claim "universe unmodified" is withdrawn if it ever fails).
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from tools import hv4_cards as C
from tools import hv4_chain as CHAIN  # noqa: F401  imported (never edited) so the frozen-sha claim covers it; the runner (build item 4) reads chain facts through it

MIN_MS = C.MIN_MS
PERSONA = "GEN"

# Frozen HV4 sha and the blobs HV6 depends on (git blob sha1; `git rev-parse 9ed4c5ff:tools/hv4_cards.py`).
HV4_FROZEN_SHA = "9ed4c5ff63935696b2a89150e13565e123047754"
HV4_BLOB_SHAS = {
    "tools/hv4_cards.py": "747fd996fcb8a665a8a2dcf994dc92ff3ad011c7",
    "tools/hv4_chain.py": "95556d4360695f47c8a76d92d34e13accc2cfb4f",
}

# ---------------------------------------------------------------------------------------------------------------------------------------
# Cadence and limits (DESIGN section 2; every value frozen before P0 and not changed by P0 counts)
# ---------------------------------------------------------------------------------------------------------------------------------------
SCAN_SECOND_MS = 2_000  # scan each minute at :02
BOARD_EVERY_MS = 15 * MIN_MS
# Fix B (smoke #1806, 2026-10-28): the DESIGN's 24 board cards every 15 min are 6 calls per board, 24 entry calls/h per arm, twice ENTRY_CALLS_PER_H (12): the cap
# fired 43 times against 24 chunks called and most board cards got no verdict. The board is now ONE call per board scan (BOARD_SIZE = CARDS_PER_CALL = 4),
# 4 board calls/h per arm, a third of the 12/h cap; setups (tags) keep the rest and come first in a scan's order. The DESIGN's own planning figure (board
# <= 576 cards/day) assumed 24 board cards an hour; 4 every 15 min is 384/day. A changed board value: P0 and the cards md5 re-run at the new sha.
BOARD_SIZE = 4  # top 4 uncarded pools by 60-min volume (was 24)
TOP_K_PER_SCAN = 8  # as HV4 TOP_K_PER_PERSONA (design is silent; HV4 value kept)
CARDS_PER_CALL = 4
ENTRY_CALLS_PER_H = 12
X_CAP_PER_H = 2
MIN_AGE_S = C.AGE_MIN_S  # 2 h, the hard guard (section 8.5)
BOARD_CALLS_PER_H = -(-BOARD_SIZE // CARDS_PER_CALL) * (60 * MIN_MS // BOARD_EVERY_MS)  # 4: one call per board, four boards an hour
assert BOARD_CALLS_PER_H <= ENTRY_CALLS_PER_H // 3, "the board must leave at least two thirds of the entry-call cap to setups"

GEN_CODE_LIMITS: Mapping[str, Any] = {  # section 2 table, HV6 v0 column
    "min_conviction": 3, "hard_stop_pct": -40.0, "max_hold_min": 720, "board": BOARD_SIZE, "entries_per_h": 3, "max_open": 6, "day_loss_stop_sol": 1.5,
}
GEN_KNOB_BOUNDS: Mapping[str, Any] = {"stop_pct": (-30.0, -4.0), "target_pct": (6.0, 150.0), "time_min": (10, 360), "trail_pct_after_plus50": (20.0, 40.0)}
X_EXIT: Mapping[str, Any] = {"stop_pct": -8.0, "target_pct": 20.0, "time_min": 60, "cap_per_h": X_CAP_PER_H}

# Setup priority inside GEN: HV4's own test order (PULLBACK, then BREAKOUT, else SECOND_WAVE).
SETUP_ORDER = ("PULLBACK", "BREAKOUT", "SECOND_WAVE")


# ---------------------------------------------------------------------------------------------------------------------------------------
# 2 h guard
# ---------------------------------------------------------------------------------------------------------------------------------------
def age_s_of(snap: Mapping[str, Any]) -> float | None:
    age, _known = C._age(snap)  # the very function `filters` uses
    return age


def age_guard(snap: Mapping[str, Any]) -> None:
    """Raise CardRefused(["age_under_2h"]) when the pool is under 2 h old or its age is unknown. Fail closed. Independent of `filters`' age_min check."""
    age = age_s_of(snap)
    if age is None or age < MIN_AGE_S:
        raise C.CardRefused(["age_under_2h"])


def build_card(snap: Mapping[str, Any], prints: Iterable[Any] | None, chain: Mapping[str, Any], *, as_of_ms: int, as_of_slot: int,
               setup: C.SetupTag | None = None, stake: float = C.STAKES[0], feats: Mapping[str, Any] | None = None,
               holders_max_age_ms: int | None = None) -> dict[str, Any]:
    """HV4's build_card behind the 2 h guard (checked before, and on the finished card). Card bytes are HV4's (CARD_VERSION hv4-card-1)."""
    age_guard(snap)
    card = C.build_card(snap, prints, chain, as_of_ms=as_of_ms, as_of_slot=as_of_slot, setup=setup, stake=stake, feats=feats, holders_max_age_ms=holders_max_age_ms)
    a = card.get("f", {}).get("age_s")
    if not isinstance(a, (int, float)) or isinstance(a, bool) or a < MIN_AGE_S:
        raise C.CardRefused(["age_under_2h"])
    return card


# ---------------------------------------------------------------------------------------------------------------------------------------
# GEN setups, cooldown, X cap, board
# ---------------------------------------------------------------------------------------------------------------------------------------
def gen_setup(feats: Mapping[str, Any], *, pool_id: str, as_of_ms: int, limiter: C.SetupLimiter | None = None) -> C.SetupTag | None:
    """At most ONE setup tag per pool per 30 min across CON and BIG (HV4's predicates, unmodified). The tag keeps HV4's persona (CON / BIG) because
    C.build_card validates it; the cooldown is keyed on "GEN". Does not mutate `limiter`."""
    if limiter is not None and not limiter.allowed(pool_id, PERSONA, as_of_ms):
        return None
    tags = {t.type: t for t in C.setups(feats, pool_id=pool_id, as_of_ms=as_of_ms, limiter=None)}
    for typ in SETUP_ORDER:
        if typ in tags:
            return tags[typ]
    return None


class XCap:
    """X's entry cap: at most X_CAP_PER_H entries in the trailing 60 min (HV4's `rate` gate form: entries with t > T - 60 min, count < cap)."""

    def __init__(self, cap_per_h: int = X_CAP_PER_H) -> None:
        self.cap = cap_per_h
        self.entries_ms: list[int] = []

    def remaining(self, now_ms: int) -> int:
        return max(0, self.cap - sum(1 for t in self.entries_ms if t > now_ms - 60 * MIN_MS))

    def record(self, now_ms: int) -> None:
        self.entries_ms.append(now_ms)


def is_board_second(t_ms: int) -> bool:
    """True at :02 of each 15-minute mark (HV4's test with HV6's constants)."""
    return (t_ms // 1000) % (BOARD_EVERY_MS // 1000) == SCAN_SECOND_MS // 1000


def select_board(vol60: Mapping[str, float | None], tagged: Iterable[str]) -> list[str]:
    """Top BOARD_SIZE filter-pass pools NOT tagged by a setup this scan, by 60-min volume descending, ties by pool id (HV4's board rule, size BOARD_SIZE = 4)."""
    t = set(tagged)
    rest = sorted((p for p in vol60 if p not in t), key=lambda p: (-(vol60[p] or 0.0), p))
    return rest[:BOARD_SIZE]


@dataclass
class ScanResult:
    as_of_ms: int
    candidates: list[str] = field(default_factory=list)  # filter-pass pools (age guard included)
    ranked: list[C.SetupTag] = field(default_factory=list)  # all GEN setups this scan, ranked
    tags: list[C.SetupTag] = field(default_factory=list)  # ranked[:TOP_K_PER_SCAN]
    board: list[str] = field(default_factory=list)
    x_tags: list[C.SetupTag] = field(default_factory=list)  # top-ranked tags X may take now (the cap is recorded by the caller on a real entry)
    feats: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)  # counts only


def scan(items: Iterable[tuple[Mapping[str, Any], Iterable[Any] | None]], *, as_of_ms: int, limiter: C.SetupLimiter, x_cap: XCap | None = None,
         board_on: bool = True) -> ScanResult:
    """One scan at T = as_of_ms over (snapshot, prints) pairs. Pure: no file, no network. Records the GEN cooldown for the tags it passes on (like the HV4
    runner, a setup that was ranked out does not burn the cooldown). Pools failing filters or the 2 h guard never reach features. A LeakageError is counted
    (`leaks`) and the pool skipped, as the HV4 runner does."""
    res = ScanResult(as_of_ms)
    cnt = res.counts
    for k in ("pools", "filter_fail", "age_guard", "leaks", "setups", "cooldown_blocked"):
        cnt[k] = 0
    raw: list[C.SetupTag] = []
    for item in items:
        snap, prints = item[0], item[1]
        series = item[2] if len(item) > 2 else None  # SCAN SPEED ONLY: the runner's fast path hands over the candle series it built straight from the Pool (== _series(snapshot))
        cnt["pools"] += 1
        pid = snap.get("id")
        try:
            fr = C.filters(snap, as_of_ms=as_of_ms)
            if not fr.passed:
                cnt["filter_fail"] += 1
                continue
            try:
                age_guard(snap)
            except C.CardRefused:
                cnt["age_guard"] += 1
                continue
            feats = C.features(snap, C.norm_prints(prints), as_of_ms=as_of_ms, series=series)
        except C.LeakageError:
            cnt["leaks"] += 1
            continue
        res.candidates.append(pid)
        res.feats[pid] = feats
        if not limiter.allowed(pid, PERSONA, as_of_ms):
            cnt["cooldown_blocked"] += 1
            continue
        t = gen_setup(feats, pool_id=pid, as_of_ms=as_of_ms, limiter=None)
        if t is not None:
            raw.append(t)
    cnt["setups"] = len(raw)
    res.ranked = C.rank_setups(raw)
    res.tags = res.ranked[:TOP_K_PER_SCAN]
    for t in res.tags:
        limiter.record(t.pool_id, PERSONA, as_of_ms)
    if board_on and is_board_second(as_of_ms):
        res.board = select_board({p: res.feats[p].get("vol_60m") for p in res.candidates}, {t.pool_id for t in res.tags})
    if x_cap is not None:
        res.x_tags = res.tags[: x_cap.remaining(as_of_ms)]
    cnt["candidates"], cnt["tags"], cnt["board"], cnt["x_tags"] = len(res.candidates), len(res.tags), len(res.board), len(res.x_tags)
    return res


# ---------------------------------------------------------------------------------------------------------------------------------------
# Path allowlist guard (DESIGN section 8.2): allowlists first, then a denylist
# ---------------------------------------------------------------------------------------------------------------------------------------
HV6_ROOT = "/data/mal/arena/haiku-v6"
TAPE_ROOT = "/data/mal/arena/tape"
RADAR_OUT_DIR = "/data/mal/change-radar-arena1"
READ_ROOTS = (TAPE_ROOT, RADAR_OUT_DIR, HV6_ROOT)  # HV6's own chain cache lives under HV6_ROOT
WRITE_ROOTS = (HV6_ROOT,)
# The bare tokens ledger / decision / score are deliberately NOT here (they would block HV6's own files); the allowlist already excludes other books' paths.
DENY_TOKENS = ("hv4", "haiku-v4", "q50", "bots.json", "shadow", "canary", "forward-", "truth-", "fresh-08", "exp011-", "tip-tape-archive",
               "/var/lib/mal/sealed", "meta-capture", "blocks/forward-1016")


class PathRefused(PermissionError):
    pass


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root + "/")


def check(path: str | os.PathLike, mode: str = "r") -> str:
    """Return the normalised path if allowed, else raise PathRefused (the message names the rule, never the path or any content).
    mode "r" reads, "w" writes/creates. A symlink resolving outside the allowlist, or into a denied token, is refused too."""
    if mode not in ("r", "w"):
        raise PathRefused("bad_mode")
    p = os.fspath(path)
    if not isinstance(p, str) or not p or "\x00" in p or not os.path.isabs(p):
        raise PathRefused("path_not_absolute")
    p = os.path.normpath(p)
    roots = WRITE_ROOTS if mode == "w" else READ_ROOTS
    for cand in (p, os.path.realpath(p)):
        if not any(_under(cand, r) for r in roots):
            raise PathRefused("not_allowlisted")
        low = cand.lower()
        if any(tok in low for tok in DENY_TOKENS):
            raise PathRefused("denied_token")
    return p


def guarded_open(path: str | os.PathLike, mode: str = "r", *args: Any, **kw: Any):
    m = "w" if any(c in mode for c in "wax+") else "r"
    return open(check(path, m), mode, *args, **kw)


# ---------------------------------------------------------------------------------------------------------------------------------------
# Import guard: HV6 code may import only hv4_cards / hv4_chain from HV4; modules that read outputs are refused
# ---------------------------------------------------------------------------------------------------------------------------------------
HV4_IMPORT_ALLOWED = frozenset({"hv4_cards", "hv4_chain", "hv4_prefetch"})  # hv4_prefetch: pure code, imports hv4_cards only, I/O only on the paths it is given
# QP (PR #911 C3, runner binding): arena modules that READ outputs (ledgers, fills, scores, gaps) are refused too. The runner imports arena_paper (the pricing
# engine), arena_state (the tape reader) and arena_declog (the pure hash-chain log), which read no output of another book.
ARENA_READER_MODULES = frozenset({"arena_score", "arena_am6_read", "arena_report_legs", "arena_worst_gaps"})


# HV4 modules HV6 imports as CODE ONLY (pure functions and constants; none is called on an HV4 ledger, chain output or score). Enumerated, checked over every
# tools/hv6_*.py by tools/test_hv6_review.py through forbidden_imports(src, HV4_CODE_ONLY); the default (no exemption) is unchanged. May conflict with PR #918 (it widens this guard too); the second PR to merge rebases.
HV4_CODE_ONLY = frozenset({"hv4_arms", "hv4_budget", "hv4_model", "hv4_prompts", "hv4_review", "hv4_validate"})


def forbidden_imports(source: str, code_only: frozenset = frozenset()) -> list[str]:
    """HV4 modules imported by `source` other than hv4_cards / hv4_chain (e.g. hv4_read, hv4_runner, hv4_shakedown_report, hv4_p0_count, hv4_review*, which
    read ledgers or outputs). Catches `import tools.hv4_x`, `from tools import hv4_x` and `from tools.hv4_x import y`."""
    bad: list[str] = []
    for node in ast.walk(ast.parse(source)):
        parts: list[str] = []
        if isinstance(node, ast.Import):
            for a in node.names:
                parts += a.name.split(".")
        elif isinstance(node, ast.ImportFrom):
            parts += (node.module or "").split(".")
            parts += [a.name for a in node.names]
        for seg in parts:
            if ((seg.startswith("hv4_") and seg not in HV4_IMPORT_ALLOWED and seg not in code_only) or seg in ARENA_READER_MODULES) and seg not in bad:
                bad.append(seg)
    return bad
