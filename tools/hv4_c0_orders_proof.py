"""hv4_c0_orders_proof: the md5 "orders equal rows" decision-equivalence replay of the HV4 C0 bridge (QP #959 removal precondition 14;
ARTIFACTS/lab/c0-readiness-2026-10-25.md section 7 item 13). SYNTHETIC log only. No network, no key, no wallet, no HV4 row, no arena path.

The bridge makes no decisions of its own: it maps decision rows to orders under the C0 caps. So the proof is that the set of orders it WOULD send is exactly the set
that follows from the rows and the caps. This tool

  1. writes a synthetic HV4-format decision log with tools.arena_declog.DecisionLog into a temp root that carries SYNTHETIC_FIXTURE_ROOT (a `start` row that repeats the
     frozen sha 9ed4c5ff and the config pin 1e611a5a, entry rows for an X arm with card sizes 0.25 / 0.5 / 1.0, exit rows, skip rows, a universe-out row, a stale row,
     a duplicate, an over-cap burst of 31 entries in one UTC day, and a second UTC day);
  2. runs the REAL tools.arena_live_bridge.Bridge in DRY with profile hv4_c0 over it, one row per tick, with a fake clock (nothing in the bridge is patched; the dry port is
     the bridge's own DryPort plus the pool facts a live port would read from the chain);
  3. derives the same orders independently from the rows read back from disk and the C0 caps TYPED HERE from DEC-032 s3 (no import of the bridge's constants, no call into the
     bridge or into tools.hv4_live_c0), and compares canonical lists of (kind, order_id, mint, S lamports, decision seq, sell reason) by md5.

Exit 0 only when the orders lists AND the per-entry outcome lists (order or the refusal label) are EQUAL, and the bridge ended with no latch.

WHAT IS SET, honestly: tools.hv4_live_c0.WINDOW_DIR (the 14-day window file directory, an absolute path constant with no config key) is pointed at a temp dir for the run and
restored after it, so a proof never creates a window file on the host. The recorded-read record is read through the bridge's own `synthetic_ok` path (a DRY start on a root with
the marker). For the gap case only, FEED_GAP_MANUAL_REVIEW is set per run and restored (above). Nothing else is replaced.

GAP ROWS (QP standing condition C-b, #1104 N5): a second, short scenario with a decision-stream `gap` row is run twice, with tools.hv4_live_c0.FEED_GAP_MANUAL_REVIEW
set to True (DEC-032 s5 item 2 as built: feed_break latches) and to False (DEC-031 Am.21 3.1: 60 s hold from receipt and the per-entry `feed_gap`), each restored after its
run; both must be EQUAL. The main scenario and its md5s are unchanged.

NOT COVERED: gates (parity, twin, tie-out: they can only refuse more, never change S), the live port, backstop sells (a clean synthetic book has none; one in the list is a DIFF),
order_refused rows, tape-gap / forwarder gap streams (the gap_tick path; tools/test_hv4_c0_gap_hold.py), a restart mid-log, the 5,000 ms stale bound at a boundary, the total-stop / wallet-floor refusals (never binding under max_open 2), partial fills.
This is a build check of the mapping. It is not gate evidence and says nothing about any book's result. No book has cleared the promotion gate.

    python -m tools.hv4_c0_orders_proof [--json OUT] [--keep]
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Mapping

from tools import arena_live_bridge as alb
from tools import hv4_live_c0 as c0
from tools.arena_declog import DecisionLog, LogChainError

SCHEMA = "hv4_c0_orders_proof_v1"
BOT_ID = "hv4-SYNTH-X"  # an X arm by suffix (hv4_live_c0.arm_refusal); not one of the nine registered arms, so nothing here can be mistaken for a real book
SEALED_PREFIX = "/data/mal/arena"  # a string for the refusal only; the tool never opens it

# --- pins typed here (from the brief / hv4-registration-1023.md): the proof fails if the module under test no longer carries them -----------------------------------------
EXPECTED_FROZEN_SHA = "9ed4c5ff63935696b2a89150e13565e123047754"
EXPECTED_CONFIG_PIN = "1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986"

# --- the reference model's caps, typed here from DEC-032 s3 (C0 column) and the HV4 registration. It must not import them from the bridge or from tools.hv4_live_c0. ------
M_STAKE_CAP_LAMPORTS = 50_000_000  # S = min(card stake, 0.05 SOL)
M_MAX_OPEN = 2
M_MAX_TRADES_PER_DAY = 30
M_MAX_DECISION_AGE_MS = 5_000
M_V_BAND = (17_500_000_000, 17_700_000_000)
M_WSOL = "So11111111111111111111111111111111111111112"
M_SIZES = (0.25, 0.5, 1.0)

BASE_MS = 1_791_331_200_000 - 3_600_000  # 2026-10-06T23:00:00Z: the first day ends an hour into the log
MIDNIGHT_MS = 1_791_331_200_000  # 2026-10-07T00:00:00Z
LATENCY_MS = 150  # row written -> row consumed by the bridge
STEP_MS = 10_000
MAX_HOLD_MS = 600_000
WINDOW_DAYS = 13  # end_ms, inside the 14-day cap
READ_BOUNDARY_MS = 1_791_000_000_000

M_GAP_HOLD_MS = 60_000  # DEC-031 Am.21 3.1: no new buy for 60 s after a coverage-losing record reaches the bridge (typed here, not imported)
OK_V = 17_600_000_000  # inside the band
LOW_V = 17_400_000_000  # universe-out


class Step:
    """One row of the synthetic log: written at `ms`, consumed by the bridge `lat` ms later (a stale row has a long lag)."""

    def __init__(self, ms: int, body: dict[str, Any], lat: int = LATENCY_MS) -> None:
        self.ms, self.body, self.lat = ms, body, lat


def build_scenario() -> tuple[list[Step], dict[str, tuple[str, int]]]:
    """(steps, pools). `pools` maps a pool id to (quote mint, V lamports): the chain facts a live port reads from the pool account."""
    steps: list[Step] = []
    pools: dict[str, tuple[str, int]] = {}
    t = [BASE_MS]

    def at(gap: int = STEP_MS) -> int:
        t[0] += gap
        return t[0]

    def pool(pid: str, quote: str = M_WSOL, v: int = OK_V) -> str:
        pools[pid] = (quote, v)
        return pid

    def entry(oid: str, pid: str, mint: str, card: float | None, size: float | None, ms: int | None = None, lat: int = LATENCY_MS, **meta: Any) -> None:
        body: dict[str, Any] = {"kind": "entry", "order_id": oid, "pool": pid, "meta": {"mint": mint, **meta}, "decision_slot": 1000 + len(steps), "attempt_key": None}
        if card is not None:
            body["stake_sol"] = card
        if size is not None:
            body["size"] = size
        steps.append(Step(at() if ms is None else ms, body, lat))

    def exit_(oid: str, pid: str, reason: str, **kw: Any) -> None:
        steps.append(Step(at(), {"kind": "exit", "order_id": oid, "pool": pid, "reason": reason, "decision_slot": 1000 + len(steps), "attempt": 1, **kw}))

    def skip(pid: str, why: str) -> None:
        steps.append(Step(at(), {"kind": "skip", "pool": pid, "reason": why}))

    # --- day 1, the story rows -------------------------------------------------------------------------------------------------------------------------------------------
    entry("e-early", pool("P-early"), "M-early", 0.25, 0.25)  # before the start row: no order
    steps.append(Step(at(), {"kind": "start", "binding": "synthetic", "frozen_sha": EXPECTED_FROZEN_SHA, "config_sha256": EXPECTED_CONFIG_PIN, "rules": [BOT_ID]}))
    skip(pool("P-skip"), "universe_v_out_of_range")  # a skip row never becomes an order
    entry("o1", pool("P1"), "M1", 0.25, 0.25)  # card 0.25 SOL -> S 0.05 SOL
    entry("o2", pool("P2"), "M2", 0.5, 0.5)  # card 0.5 -> S 0.05
    entry("o3", pool("P3"), "M3", 1.0, 1.0)  # card 1.0, two already open -> max_open
    exit_("o1", "P1", "tp")
    entry("o3b", "P3", "M3", 1.0, 1.0)  # same pool and mint as the refused o3, new order id: o3 never bought it
    exit_("o2", "P2", "sl", frac=0.5)  # a partial exit: v1 refuses, the position stays open
    exit_("o2", "P2", "sl")
    exit_("o2", "P2", "sl")  # the position is closed already: ignored
    exit_("zz-unknown", "P-zz", "tp")  # no such order: ignored
    entry("o1", "P1", "M1", 0.25, 0.25)  # the same order_id again: a duplicate
    entry("o4", "P1", "M1", 0.25, 0.25)  # the mint was bought before (and sold): never re-bought
    entry("o5", pool("P5", v=LOW_V), "M5", 0.5, 0.5)  # V below the band: universe-out
    entry("o6", pool("P6", quote="So22222222222222222222222222222222222222222"), "M6", 0.5, 0.5)  # quote is not WSOL: universe-out
    entry("o7", pool("P7"), "M7", 0.5, 0.5, lat=6_000)  # consumed 6 s after the row was written: older than the 5 s cap
    entry("o8", pool("P8"), "M8", 0.3, 0.3)  # a size that is not one of the three: refused
    entry("o9", pool("P9"), "M9", 0.03, None)  # no size key, card 0.03 SOL below the cap: S = the card (not an HV4 row shape; it exercises min() the other way)
    exit_("o3b", "P3", "time")
    exit_("o9", "P9", "tp")
    entry("o10", pool("P10"), "M10", 0.0, 0.5)  # a card stake that is not positive (nothing is open now, so it is the card that refuses it)
    # --- the daily cap: 26 more accepted pairs make 30 accepted on this UTC day; the 31st entry is refused -------------------------------------------------------------
    for i in range(26):
        size = M_SIZES[i % 3]
        entry(f"c{i}", pool(f"PC{i}"), f"MC{i}", size, size)
        exit_(f"c{i}", f"PC{i}", "tp")
    entry("c26", pool("PC26"), "MC26", 0.5, 0.5)  # the 31st on the day
    skip("PC26", "universe_v_out_of_range")
    # --- day 2: the cap counts again -------------------------------------------------------------------------------------------------------------------------------------
    entry("d1", pool("PD1"), "MD1", 0.5, 0.5, ms=MIDNIGHT_MS + 120_000)
    t[0] = MIDNIGHT_MS + 120_000
    exit_("d1", "PD1", "tp")
    entry("d2", pool("PD2"), "MD2", 0.25, 0.25)  # left open at the end of the log: no sell follows
    return steps, pools


def build_gap_scenario() -> tuple[list[Step], dict[str, tuple[str, int]]]:
    """QP standing condition C-b (#1104 N5): a decision-stream `gap` row inside a short log, so the gap refusal labels are inside the outcomes md5. Run under BOTH values
    of tools.hv4_live_c0.FEED_GAP_MANUAL_REVIEW (run_proof). The rows: start; an order opened before the gap; the gap row at G; an entry decided before G but read after
    it (lat 1 s); an entry inside the 60 s hold; the exit of the pre-gap order (exits continue in both modes); an entry decided before G and read after the hold (it is
    refused per entry under Am.21 before the age check); an entry decided after the hold."""
    steps: list[Step] = []
    pools: dict[str, tuple[str, int]] = {}
    g0 = MIDNIGHT_MS + 6 * 3_600_000

    def entry(oid: str, ms: int, lat: int = LATENCY_MS) -> None:
        pools[f"PG-{oid}"] = (M_WSOL, OK_V)
        steps.append(Step(ms, {"kind": "entry", "order_id": oid, "pool": f"PG-{oid}", "meta": {"mint": f"MG-{oid}"}, "decision_slot": 5000 + len(steps), "stake_sol": 0.25,
                               "size": 0.25, "attempt_key": None}, lat))

    steps.append(Step(g0 - 60_000, {"kind": "start", "binding": "synthetic", "frozen_sha": EXPECTED_FROZEN_SHA, "config_sha256": EXPECTED_CONFIG_PIN, "rules": [BOT_ID]}))
    entry("g-pre", g0 - 30_000)  # opened before the gap
    steps.append(Step(g0, {"kind": "gap", "reason": "synthetic"}))  # the coverage-losing record, instant G = g0
    entry("g-before-late", g0 - 500, lat=1_000)  # decided before G, read 0.5 s after G (inside the hold)
    entry("g-in-hold", g0 + 20_000)  # decided after G, read inside the hold
    steps.append(Step(g0 + 30_000, {"kind": "exit", "order_id": "g-pre", "pool": "PG-g-pre", "reason": "tp", "decision_slot": 5100, "attempt": 1}))
    entry("g-before-after-hold", g0 - 1, lat=M_GAP_HOLD_MS + 1_000)  # decided before G, read after the hold: per entry under Am.21, before the age check
    entry("g-after", g0 + M_GAP_HOLD_MS + 5_000)  # decided after the hold
    return steps, pools


class SyntheticDryPort(alb.DryPort):
    """The bridge's own DryPort, plus the pool facts a live port reads from the chain (pool -> quote mint, V). The universe rule applied is the one the live port applies."""

    def __init__(self, balance_lamports: int, pools: Mapping[str, tuple[str, int]]) -> None:
        super().__init__(balance_lamports)
        self.pools = dict(pools)

    def universe_problem(self, pool: str, mint: str) -> str | None:
        facts = self.pools.get(pool)
        return c0.universe_problem(facts[0], facts[1]) if facts else "pool_unreadable:synthetic"


class Clock:
    def __init__(self, t: int) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


@contextlib.contextmanager
def _window_dir(path: str) -> Iterator[None]:
    """Point hv4_live_c0.WINDOW_DIR at a temp dir and restore it. The only module constant this tool replaces (see the module doc)."""
    old = c0.WINDOW_DIR
    c0.WINDOW_DIR = path
    try:
        yield
    finally:
        c0.WINDOW_DIR = old


class BridgeRun:
    def __init__(self) -> None:
        self.would_send: list[dict[str, Any]] = []
        self.log: list[dict[str, Any]] = []
        self.latches: dict[str, str] = {}
        self.consume_ms: dict[int, int] = {}
        self.rows: list[dict[str, Any]] = []
        self.pools: dict[str, tuple[str, int]] = {}
        self.log_md5 = ""
        self.chain_head = ""
        self.counts: dict[str, int] = {}


def run_bridge(work: Path, steps: list[Step], pools: Mapping[str, tuple[str, int]]) -> BridgeRun:
    """Write the synthetic log row by row and run the real Bridge (DRY, hv4_c0) one tick per row."""
    root, state, reads, window = work / "arena", work / "state", work / "reads", work / "window"
    for d in (root, state, reads, window):
        d.mkdir(parents=True)
    for d in (reads, window):
        os.chmod(d, 0o755)
    (root / c0.SYNTHETIC_MARKER).write_text("")
    rec = {"bot_id": BOT_ID, "status": "PASS", "read_boundary_ms": READ_BOUNDARY_MS, "attempts": 1, "model_ids": [], "am15": {"rule": "synthetic"}, "frozen_sha": EXPECTED_FROZEN_SHA}
    (reads / f"{BOT_ID}.json").write_text(json.dumps(rec, sort_keys=True) + "\n")
    os.chmod(reads / f"{BOT_ID}.json", 0o644)
    clock = Clock(BASE_MS)
    raw = {"bot_id": BOT_ID, "frozen_sha": EXPECTED_FROZEN_SHA, "root": str(root), "state_dir": str(state), "end_ms": BASE_MS + WINDOW_DAYS * 86_400_000, "max_hold_ms": MAX_HOLD_MS,
           "stop_file": str(work / "STOP"), "halt_file": str(work / "HALT"), "mode": "dry", "profile": c0.PROFILE, "funded_lamports": 600_000_000, "reads_dir": str(reads)}
    out = BridgeRun()
    out.pools = dict(pools)
    port = SyntheticDryPort(600_000_000, pools)
    declog = DecisionLog(str(root))
    with _window_dir(str(window)):
        cfg = alb.Config.from_dict(raw)
        bridge = alb.Bridge(cfg, port, now_ms=clock, live_ok_path=work / "LIVE_OK", tier_path=work / "TIER", root_checks=False)
        for st in steps:
            row = declog.append(BOT_ID, st.ms, st.body)
            clock.t = st.ms + st.lat
            out.consume_ms[row["seq"]] = clock.t
            bridge.tick()
    out.would_send = list(port.would_send)
    out.latches = dict(bridge.state.latches)
    out.counts = dict(bridge.state.counts)
    lp = state / "bridge-log-dry.jsonl"
    out.log = [json.loads(x) for x in lp.read_text().splitlines()] if lp.exists() else []
    check = DecisionLog(str(root))
    out.chain_head = check.verify(BOT_ID)[0]  # the chain verifies from disk (raises LogChainError otherwise)
    out.rows = check.rows(BOT_ID)
    out.log_md5 = check.md5()
    return out


# --- the two canonical lists ---------------------------------------------------------------------------------------------------------------------------------------------
Order = tuple[str, str, str, int, int, str]  # (kind, order_id, mint, S lamports (0 for a sell), decision seq, sell reason ("" for a buy))
Outcome = tuple[int, str]  # (entry row seq, "order" | refusal label)


def bridge_lists(run: BridgeRun) -> tuple[list[Order], list[Outcome], list[str]]:
    """Orders and outcomes as the BRIDGE reports them: the port's would-send list tied to the bridge log's buy_sent / sell_sent rows (which carry the decision seq). A
    disagreement between the two bridge-side views is returned as a problem."""
    problems: list[str] = []
    sent = [r for r in run.log if r["kind"] in ("buy_sent", "sell_sent")]
    if len(sent) != len(run.would_send):
        problems.append(f"bridge log has {len(sent)} send rows, the port would send {len(run.would_send)}")
    orders: list[Order] = []
    for w, r in zip(run.would_send, sent):
        kind = "buy" if w["side"] == "buy" else "sell"
        if r["kind"] != f"{kind}_sent" or r.get("order_id") != w["order_id"] or r.get("mint") != w["mint"] or r.get("would_send") is not True:
            problems.append(f"port and log disagree on {w}")
        if kind == "buy":
            if r.get("stake_lamports") != w["stake_lamports"]:
                problems.append(f"stake differs between port and log for {w['order_id']}: {w['stake_lamports']} vs {r.get('stake_lamports')}")
            orders.append(("buy", w["order_id"], w["mint"], int(w["stake_lamports"]), int(r.get("decision_seq", -1)), ""))
        else:
            orders.append(("sell", w["order_id"], w["mint"], 0, int(r.get("decision_seq") if r.get("decision_seq") is not None else -1), str(w["reason"])))
    outcomes: list[Outcome] = []
    for r in run.log:
        if r["kind"] == "buy_sent":
            outcomes.append((int(r.get("decision_seq", -1)), "order"))
        elif r["kind"] == "entry_refused":
            outcomes.append((int(r["seq"]), str(r["why"])))
        elif r["kind"] == "universe_skip":
            outcomes.append((int(r["seq"]), "universe_skip:" + str(r["why"])))
        elif r["kind"] == "duplicate_entry":
            outcomes.append((int(r["seq"]), "duplicate_entry"))
    return orders, sorted(outcomes), problems


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _card_lamports(card: Any) -> int | None:
    """Lamports of a card stake, with Decimal (so no float rounding); None when it is not a positive number."""
    if isinstance(card, bool) or not isinstance(card, (int, float)) or card != card or card <= 0:
        return None
    return int((Decimal(str(card)) * 1_000_000_000).to_integral_value())


def expected_lists(rows: list[dict[str, Any]], consume_ms: Mapping[int, int], pools: Mapping[str, tuple[str, int]], gap_mode: str | None = None) -> tuple[list[Order], list[Outcome]]:
    """The orders (and per-entry outcomes) that follow from the rows and the C0 caps. Written from DEC-032 s3 and the row semantics, not from the bridge's code: it uses
    only the constants above, the rows as read back from disk and the instants at which the bridge consumed them. `gap_mode` (C-b): None = the log has no gap row;
    "manual" = DEC-032 s5 item 2 as built (the first gap row latches feed_break: every later entry is `latched:feed_break`, exits continue); "am21" = DEC-031 Am.21 3.1
    (no new buy until max(G, receipt) + M_GAP_HOLD_MS: `feed_gap_hold`; an entry decided at or before the newest G: `feed_gap`; both before the age check)."""
    orders: list[Order] = []
    outcomes: list[Outcome] = []
    start_seen = False
    frozen_ok = True
    gap_latched = False
    gap_newest = 0
    hold_until = 0
    created: dict[str, tuple[str, str]] = {}  # order_id -> (mint, "open" | "closed")
    bought: set[str] = set()
    per_day: Counter[str] = Counter()
    for r in rows:
        kind, seq = r.get("kind"), r["seq"]
        if kind == "start":
            start_seen = True
            frozen_ok = frozen_ok and r.get("frozen_sha") == EXPECTED_FROZEN_SHA
        elif kind == "gap" and gap_mode is not None and isinstance(r.get("ms"), int):
            g = int(r["ms"])
            if gap_mode == "manual":
                gap_latched = True
            else:
                gap_newest = max(gap_newest, g)
                hold_until = max(hold_until, max(g, consume_ms[seq]) + M_GAP_HOLD_MS)
        elif kind == "entry":
            oid, now = str(r["order_id"]), consume_ms[seq]
            mint = str((r.get("meta") or {}).get("mint") or f"pool:{r.get('pool')}")
            label: str | None = None
            if oid in created:
                label = "duplicate_entry"
            elif not start_seen:
                label = "no_start_row"
            elif not frozen_ok:
                label = "latched:frozen_sha_mismatch"
            elif gap_latched:
                label = "latched:feed_break"
            elif gap_mode == "am21" and now < hold_until:
                label = "feed_gap_hold"
            elif gap_mode == "am21" and gap_newest and int(r["ms"]) <= gap_newest:
                label = "feed_gap"
            elif now - int(r["ms"]) > M_MAX_DECISION_AGE_MS:
                label = "stale_decision"
            elif sum(1 for _m, st in created.values() if st == "open") >= M_MAX_OPEN:
                label = "max_open"
            elif per_day[_utc_day(now)] >= M_MAX_TRADES_PER_DAY:
                label = "max_trades_per_day"
            elif mint in bought:
                label = "already_bought"
            else:
                quote, v = pools[str(r.get("pool"))]
                if quote != M_WSOL:
                    label = "universe_skip:quote_not_wsol"
                elif not M_V_BAND[0] <= v <= M_V_BAND[1]:
                    label = "universe_skip:universe_v_out_of_range"
            s = None
            if label is None:
                size = r.get("size")
                card = r["stake_sol"] if "stake_sol" in r else size
                if size is not None and (isinstance(size, bool) or not isinstance(size, (int, float)) or float(size) not in M_SIZES):
                    label = "size_invalid"
                elif _card_lamports(card) is None:
                    label = "card_stake_invalid"
                else:
                    s = min(_card_lamports(card) or 0, M_STAKE_CAP_LAMPORTS)
            if label is None and s is not None:
                orders.append(("buy", oid, mint, s, seq, ""))
                created[oid] = (mint, "open")
                bought.add(mint)
                per_day[_utc_day(now)] += 1
                outcomes.append((seq, "order"))
            else:
                outcomes.append((seq, label or "unknown"))
        elif kind == "exit":
            oid = str(r["order_id"])
            if oid not in created or created[oid][1] != "open":
                continue
            frac = r.get("frac", 1.0)
            if isinstance(frac, bool) or not isinstance(frac, (int, float)) or frac < 1.0:
                continue  # v1 sells everything or nothing
            mint = created[oid][0]
            orders.append(("sell", oid, mint, 0, seq, str(r.get("reason") or "book_exit")))
            created[oid] = (mint, "closed")
    return orders, sorted(outcomes)


def canonical(items: list[tuple[Any, ...]]) -> str:
    return "".join(json.dumps(list(i), separators=(",", ":")) + "\n" for i in items)


def md5_of(items: list[tuple[Any, ...]]) -> str:
    return hashlib.md5(canonical(items).encode()).hexdigest()


def diff_lines(a: list[tuple[Any, ...]], b: list[tuple[Any, ...]], limit: int = 8) -> list[str]:
    out: list[str] = []
    for i in range(max(len(a), len(b))):
        x, y = (a[i] if i < len(a) else None), (b[i] if i < len(b) else None)
        if x != y:
            out.append(f"  #{i}: bridge={x} rows={y}")
            if len(out) >= limit:
                out.append("  ...")
                break
    return out


def module_sha256(mod: Any) -> str:
    return hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()


class ProofResult:
    def __init__(self) -> None:
        self.ok = False
        self.lines: list[str] = []
        self.record: dict[str, Any] = {}


GAP_MODES = (("manual", True), ("am21", False))  # (label, the value of tools.hv4_live_c0.FEED_GAP_MANUAL_REVIEW for the run)


def run_gap_case(work: Path, mode: str, manual: bool) -> dict[str, Any]:
    """C-b: the gap scenario under one FEED_GAP_MANUAL_REVIEW value (set for this run only and restored). Returns the md5s, the labels and any problem."""
    steps, pools = build_gap_scenario()
    old = c0.FEED_GAP_MANUAL_REVIEW
    c0.FEED_GAP_MANUAL_REVIEW = manual
    try:
        run = run_bridge(work, steps, pools)
    finally:
        c0.FEED_GAP_MANUAL_REVIEW = old
    b_orders, b_out, problems = bridge_lists(run)
    e_orders, e_out = expected_lists(run.rows, run.consume_ms, run.pools, gap_mode=mode)
    want_latches = {"feed_break"} if manual else set()
    if set(run.latches) != want_latches:
        problems.append(f"gap/{mode}: the bridge ended with latches {sorted(run.latches)}, expected {sorted(want_latches)}")
    if any(w.get("reason") in ("time_stop_backstop", "sl_backstop") for w in run.would_send):
        problems.append(f"gap/{mode}: a backstop sell is in the would-send list")
    return {"mode": mode, "bo": md5_of(b_orders), "eo": md5_of(e_orders), "bq": md5_of(b_out), "eq": md5_of(e_out), "problems": problems,
            "labels": dict(sorted(Counter(lab for _s, lab in e_out).items())), "b_orders": b_orders, "e_orders": e_orders, "b_out": b_out, "e_out": e_out}


def run_proof(work: Path | None = None) -> ProofResult:
    """Run the whole proof in `work` (a fresh temp dir when None, removed after). Never raises for a DIFF; it returns ok=False and the reasons."""
    res = ProofResult()
    problems: list[str] = []
    for what, have, want in (("FROZEN_SHA", c0.FROZEN_SHA, EXPECTED_FROZEN_SHA), ("CONFIG_PIN_SHA256", c0.CONFIG_PIN_SHA256, EXPECTED_CONFIG_PIN)):
        if have != want:
            problems.append(f"tools.hv4_live_c0.{what} is {have}, the proof expects {want}")
    steps, pools = build_scenario()
    own = work is None
    base = Path(tempfile.mkdtemp(prefix="hv4-c0-orders-proof-")) if own else Path(work)  # type: ignore[arg-type]
    try:
        run = run_bridge(base, steps, pools)
        gap_runs = []
        for mode, manual in GAP_MODES:
            gw = base / f"gap-{mode}"
            gw.mkdir()
            gap_runs.append(run_gap_case(gw, mode, manual))
    except (ValueError, SystemExit, LogChainError) as exc:  # the bridge refused to start (config, pin, window, read record): a DIFF with the reason, not a traceback
        res.lines += ["hv4_c0_orders_proof schema=" + SCHEMA, "PROBLEM the bridge refused the synthetic start: " + "; ".join(problems + [f"{type(exc).__name__}: {exc}"]),
                      "RESULT DIFF (no orders were compared; synthetic log; not gate evidence)"]
        res.record = {"schema": SCHEMA, "result": "DIFF", "problems": problems + [f"{type(exc).__name__}: {exc}"]}
        return res
    finally:
        if own:
            shutil.rmtree(base, ignore_errors=True)
    b_orders, b_out, b_problems = bridge_lists(run)
    e_orders, e_out = expected_lists(run.rows, run.consume_ms, run.pools)
    problems += b_problems
    if run.latches:
        problems.append(f"the bridge ended with latches {run.latches} (a host without the sealed arena prefix fails the alias guard closed: tools.hv4_live_c0._alias_of_sealed)")
    if any(w.get("reason") in ("time_stop_backstop", "sl_backstop") for w in run.would_send):
        problems.append("a backstop sell is in the would-send list: it has no row behind it")
    bo, eo, bq, eq = md5_of(b_orders), md5_of(e_orders), md5_of(b_out), md5_of(e_out)  # type: ignore[arg-type]
    kinds = Counter(r.get("kind") for r in run.rows)
    for g in gap_runs:
        problems += g["problems"]
    gaps_equal = all(g["bo"] == g["eo"] and g["bq"] == g["eq"] for g in gap_runs)
    equal = bo == eo and bq == eq and gaps_equal and not problems
    L = res.lines
    L.append(f"hv4_c0_orders_proof schema={SCHEMA}")
    L.append("git_sha=" + os.environ.get("PROOF_GIT_SHA", "unknown"))
    for mod in (alb, c0):
        L.append(f"code_sha256 {Path(mod.__file__).name}={module_sha256(mod)}")
    L.append(f"code_sha256 hv4_c0_orders_proof.py={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}")
    L.append(f"bot_id={BOT_ID} frozen_sha={EXPECTED_FROZEN_SHA} config_pin={EXPECTED_CONFIG_PIN}")
    L.append(f"synthetic log: rows={len(run.rows)} kinds={dict(sorted(kinds.items()))} utc_days=2 log_md5={run.log_md5} chain_head={run.chain_head}")
    nb = Counter(o[0] for o in b_orders)
    ne = Counter(o[0] for o in e_orders)
    L.append(f"bridge_orders count={len(b_orders)} buys={nb['buy']} sells={nb['sell']} stakes={sorted({o[3] for o in b_orders if o[0] == 'buy'})}")
    L.append(f"bridge_orders_md5={bo}")
    L.append(f"expected_orders count={len(e_orders)} buys={ne['buy']} sells={ne['sell']} stakes={sorted({o[3] for o in e_orders if o[0] == 'buy'})}")
    L.append(f"expected_orders_md5={eo}")
    labels = Counter(lab for _s, lab in e_out)
    L.append(f"entry_outcomes count={len(b_out)} by_label={dict(sorted(labels.items()))}")
    L.append(f"bridge_outcomes_md5={bq}")
    L.append(f"expected_outcomes_md5={eq}")
    for g in gap_runs:  # C-b: the gap-row case under both FEED_GAP_MANUAL_REVIEW values
        L.append(f"gap_case mode={g['mode']} bridge_orders_md5={g['bo']} expected_orders_md5={g['eo']} bridge_outcomes_md5={g['bq']} expected_outcomes_md5={g['eq']} "
                 f"labels={g['labels']}")
    if not equal:
        for g in gap_runs:
            L += [f"gap/{g['mode']}: " + x for x in diff_lines(g["b_orders"], g["e_orders"]) + diff_lines(g["b_out"], g["e_out"])]
        for p in problems:
            L.append(f"PROBLEM {p}")
        L += diff_lines(b_orders, e_orders)  # type: ignore[arg-type]
        L += diff_lines(b_out, e_out)  # type: ignore[arg-type]
    L.append("RESULT " + ("EQUAL" if equal else "DIFF") + " (orders equal rows; synthetic log; not gate evidence; no book has cleared the promotion gate)")
    res.ok = equal
    res.record = {"schema": SCHEMA, "result": "EQUAL" if equal else "DIFF", "bot_id": BOT_ID, "git_sha": os.environ.get("PROOF_GIT_SHA", "unknown"),
                  "rows": len(run.rows), "log_md5": run.log_md5, "orders": len(b_orders), "buys": nb["buy"], "sells": nb["sell"],
                  "bridge_orders_md5": bo, "expected_orders_md5": eo, "bridge_outcomes_md5": bq, "expected_outcomes_md5": eq, "problems": problems,
                  "gap_cases": [{k: g[k] for k in ("mode", "bo", "eo", "bq", "eq", "labels")} for g in gap_runs]}
    return res


def tmp_base_refusal(base: str) -> str | None:
    """The temp base may not be the sealed arena prefix or under it. Textual (normpath), so it touches nothing."""
    p = os.path.abspath(base)
    return "tmp_base_under_sealed_arena" if p == SEALED_PREFIX or p.startswith(SEALED_PREFIX + "/") else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    ap.add_argument("--json", help="write the result record here")
    ap.add_argument("--keep", action="store_true", help="keep the temp work dir and print its path")
    a = ap.parse_args(argv)
    why = tmp_base_refusal(tempfile.gettempdir())
    if why:
        print(f"REFUSED: {why}")
        return 2
    work = Path(tempfile.mkdtemp(prefix="hv4-c0-orders-proof-")) / "w" if a.keep else None
    if work is not None:
        print(f"work dir kept at {work.parent}")
    res = run_proof(work)
    print("\n".join(res.lines))
    if a.json:
        Path(a.json).write_text(json.dumps(res.record, indent=1, sort_keys=True) + "\n")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
