#!/usr/bin/env python3
"""hv4_p0_count: counts-only P0 count for HV4 under DEC-031 Amendment 4 item 2 (QP-RULING-1022-hv4-am6.md, ADDENDUM 1, P1-P2).

WHAT IT IS. A separate script. It is NOT a runner replay: no ArenaEngine, no ledger, no registry, no RPC, no model, no price, no fill, no exit, no outcome.
It imports the HV4 rule code from the checkout it is pointed at (`--repo`), which must be a CLEAN checkout at the frozen sha, and it calls, per scan instant,
the pre-model rule calls that tools/hv4_runner.py makes in `Runner._scan` (cite: hv4_runner.py at 9ed4c5ff, lines 977-1060, and `_card` 937-964):

    runner line   call here
    986-1004      state.pools_wsol(pumpswap) -> lite_snapshot -> C.prescreen -> prints_15m_short   (SCAN SPEED ONLY in the runner; same result as the full filters)
    1005-1013     C.filters(snap, as_of_ms=T)                                                       (the Lane G filters; V universe v_missing / v_band)
    1018-1020     C.series_from_pool_candles, C.norm_prints, C.features
    1022          C.setups(feats, pool_id, as_of_ms=T, limiter)                                      (the 30-min cooldown limiter, as the runner)
    1023-1027     C.rank_setups, top TOP_K_PER_PERSONA (8) per persona CON / BIG, limiter.record
    1029-1033     the 15-minute board (second :02 of minutes 0/15/30/45), 10 pools by vol_60m not tagged
    937-964       the card path: C.assert_as_of, C.filters, C.code_vetoes (build_card's own calls), then C.is_safe_id

WHAT IT DOES NOT APPLY. Chain-dependent vetoes (holders, mint / freeze authority, token extensions, Mayhem, chain stamps, and the 15-minute quote reference that
the runner takes from its own QuoteHistory) are not applied. code_vetoes is called with a NEUTRAL chain (mint and freeze authority null, classic token program,
no extensions, Mayhem false, no holder facts) and the one reason that the neutral chain itself raises, `quote_15m_unknown`, is dropped. Every card that survives
is counted in `chain_veto_not_applied`. The model's choice, the risk agent's grade, the X arms' book gates (open positions, cooldown, rate, day loss) and the
engine are not applied either. So a count is an UPPER BOUND of would-be attempts (P2): "P0 is an upper bound (structure filters before the model; chain vetoes not
applied); it is anti-conservative for the floor only, which protects N and the calendar, not the gate."

TAPE FIELDS (P1). Every row is projected to a whitelist the moment it is read, so nothing else reaches the state: type, prog, t_recv_ms; trade: side, trader, mint,
pool, quote_mint, virtual_quote_reserves, slot, sol_lamports, token_raw, quote_reserve, base_reserve, lp_fee, pool_quote_amount; create: mint, trader, slot,
quote_reserve, base_reserve; complete: mint; pool_create: pool, mint, quote_mint, slot; pool_info: pool, mint, quote_mint; gap: kind, prog and the span stamps
and counts the state keeps (GAP_EXTRA); hb: prog. NOT read: creator, name, symbol, signature, event_index, event_ts, anything else.

AS-OF (guaranteed three ways). (1) The state is stepped with poll(until_ms=T - 1): no row at or after the scan instant T is applied. (2) After every poll the
tool asserts state.now_ms < T and raises AsOfError otherwise. (3) The rule code's own assert_as_of (filters, features, code_vetoes) runs on every snapshot, and
the tool calls it on the snapshot, prints and neutral chain before code_vetoes. The slot passed as as_of_slot is the highest trade slot among the rows applied.

OUTPUT. Canonical JSON lines, then a last line `sha256 <hex>` over everything before it. Counts per (UTC day, persona / arm) and gap counts. No mint, pool, wallet,
signature, card or price. Timing and memory go to stderr, never into the hashed output.

RUN. From the job: `python3 -I /path/to/hv4_p0_count.py --repo <clean checkout at the frozen sha> --tape-dir ... --days 2026-10-21,2026-10-22 --out ...`.
The script lives on its own branch and is supplied by path; it takes the rule code from --repo. Exit 2: repo not at the pinned sha, dirty, or bad arguments.
Exit 1: at least one day FAILED (missing warm-up or day tape). Exit 0 otherwise.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PINNED_SHA = "9ed4c5ff63935696b2a89150e13565e123047754"
MIN_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
SCAN_EVERY_MS = 5 * MIN_MS  # hv4_runner.py SCAN_EVERY_MS (line 66 at 9ed4c5ff)
SCAN_SECOND_MS = 2_000  # hv4_runner.py SCAN_SECOND_MS: the scanner ticks at second :02
BOARD_EVERY_MS = 15 * MIN_MS  # hv4_runner.py BOARD_EVERY_MS
BOARD_SIZE = 10
TOP_K_PER_PERSONA = 8
PERSONAS = ("CON", "BIG")  # LANE_G_PERSONAS; lane H (BST) is off in window 1 (nine arms)
ARMS = ("CON-L", "CON-F", "CON-P", "CON-X", "CON-R", "BIG-L", "BIG-F", "BIG-X", "BIG-R")
FLOOR_PER_DAY = 2.0  # Am.4 item 2.1
DATA_LOSS_MAX_S = 3600  # Am.3 item 1 gap rule, reported as a flag only
CHAIN_ONLY_REASONS = frozenset({"quote_15m_unknown"})  # raised by the neutral chain itself; the real value comes from the runner's QuoteHistory (not applied)
DISCLOSURE = ("P0 is an upper bound (structure filters before the model; chain vetoes not applied); it is anti-conservative for the floor only, "
              "which protects N and the calendar, not the gate (a3 ruling Q6).")

ROW_KEEP = {
    "trade": ("side", "trader", "mint", "pool", "quote_mint", "virtual_quote_reserves", "slot", "sol_lamports", "token_raw", "quote_reserve", "base_reserve",
              "lp_fee", "pool_quote_amount"),
    "create": ("mint", "trader", "slot", "quote_reserve", "base_reserve"),
    "complete": ("mint",),
    "pool_create": ("pool", "mint", "quote_mint", "slot"),
    "pool_info": ("pool", "mint", "quote_mint"),
    "hb": (),
}
GAP_KEYS = ("kind", "from_ms", "paused_since_ms", "to_ms", "data_loss")


class AsOfError(RuntimeError):
    """The state clock reached the scan instant: a row at or after T was applied."""


class RepoError(RuntimeError):
    """The rule checkout is not the pinned, clean one."""


# ------------------------------------------------------------------------------------------------------------------------------ repo
def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RepoError("git " + " ".join(args) + " failed")
    return r.stdout


def verify_repo(repo: str, expect_sha: str) -> str:
    """HEAD must equal the pinned sha and `git status --porcelain` must be empty, else RepoError. Returns HEAD."""
    head = _git(repo, "rev-parse", "HEAD").strip()
    if head != expect_sha:
        raise RepoError(f"repo HEAD {head[:12]} is not the pinned sha {expect_sha[:12]}")
    if _git(repo, "status", "--porcelain").strip():
        raise RepoError("repo is dirty (git status --porcelain is not empty)")
    return head


def load_rules(repo: str) -> SimpleNamespace:
    """Import tools.hv4_cards / arena_state / arena_zst from `repo` (and nothing from this script's own directory)."""
    sys.dont_write_bytecode = True  # keep the clean checkout clean
    repo = str(Path(repo).resolve())
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p and str(Path(p).resolve()) != here]
    for m in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
        del sys.modules[m]
    sys.path.insert(0, repo)
    from tools import arena_state as AS  # noqa: PLC0415
    from tools import hv4_cards as C  # noqa: PLC0415

    for mod in (AS, C):
        if not str(Path(mod.__file__).resolve()).startswith(repo + os.sep):
            raise RepoError(f"{mod.__name__} was not imported from {repo}")
    return SimpleNamespace(AS=AS, C=C, repo=repo)


# ------------------------------------------------------------------------------------------------------------------------------ state
def make_state_class(AS: Any) -> type:
    class ProjState(AS.ArenaState):
        """ArenaState that reads only the P1 whitelist, records the highest trade slot, and keeps its own gap spans."""

        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, **k)
            self.slot = 0
            self.spans: dict[tuple, list] = {}  # (prog, kind, from_ms) -> [to_ms, data_loss]

        def _ingest(self, data: bytes) -> None:  # same loop as ArenaState._ingest, plus the projection
            for ln in data.split(b"\n"):
                if not ln.strip():
                    continue
                try:
                    row = json.loads(ln)
                except ValueError:
                    self.counters["bad_lines"] += 1
                    continue
                if isinstance(row, dict) and isinstance(row.get("t_recv_ms"), int):
                    self._buf.append(project_row(row))
                else:
                    self.counters["bad_lines"] += 1

        def apply(self, row: dict) -> None:
            if row.get("type") == "trade":
                s = row.get("slot")
                if isinstance(s, int) and s > self.slot:
                    self.slot = s
            super().apply(row)

        def _gap(self, row: dict, t: int) -> None:
            super()._gap(row, t)
            prog, kind = str(row.get("prog") or "all"), row.get("kind")
            fr = next((row[k] for k in ("from_ms", "paused_since_ms") if isinstance(row.get(k), int)), t)
            to = row["to_ms"] if isinstance(row.get("to_ms"), int) else t
            sp = self.spans.setdefault((prog, kind, fr), [max(fr, to), False])
            sp[0] = max(sp[0], to)
            sp[1] = sp[1] or bool(row.get("data_loss"))

    return ProjState


def project_row(row: dict, gap_extra: tuple = ()) -> dict:
    typ = row.get("type")
    out = {"type": typ, "prog": row.get("prog"), "t_recv_ms": row["t_recv_ms"]}
    if typ == "gap":
        for k in (*GAP_KEYS, *_GAP_EXTRA):
            if k in row:
                out[k] = row[k]
        return out
    for k in ROW_KEEP.get(typ, ()):
        if k in row:
            out[k] = row[k]
    return out


_GAP_EXTRA: tuple = ()


# ------------------------------------------------------------------------------------------------------------------------------ time
def parse_day(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def hour_key(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def day_label(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def tape_check(tape_dir: str, start_ms: int, end_ms: int, warm_min: int) -> str | None:
    """None when every hour file from start - warm_min to end exists (.jsonl or .jsonl.zst); else the reason (counts only, no path)."""
    d = Path(tape_dir)
    h = (start_ms - warm_min * MIN_MS) // HOUR_MS * HOUR_MS
    missing_warm = missing_day = 0
    while h < end_ms:
        k = hour_key(h)
        if not ((d / f"tape-{k}.jsonl").exists() or (d / f"tape-{k}.jsonl.zst").exists()):
            if h < start_ms:
                missing_warm += 1
            else:
                missing_day += 1
        h += HOUR_MS
    if missing_warm:
        return f"warmup_tape_missing_hours={missing_warm}"
    if missing_day:
        return f"day_tape_missing_hours={missing_day}"
    return None


# ------------------------------------------------------------------------------------------------------------------------------ the scan
def lite_snapshot(p: Any, now_ms: int, LAMPORTS: int) -> dict[str, Any]:
    """hv4_runner.lite_snapshot (557-566 at 9ed4c5ff), same expressions. SCAN SPEED ONLY."""
    created, q, lt = p.created_ms, p.q_real, p.last_trade
    return {"now_ms": now_ms, "last_ms": p.last_ms, "ath_ms": p.ath_ms, "created_ms": created, "complete_ms": p.complete_ms, "origin_create_ms": p.origin_create_ms,
            "last_trade_ms": lt.get("t_ms") if isinstance(lt, dict) else None, "created_known": created is not None,
            "age_s": (now_ms - created) / 1000.0 if created is not None else None, "age_since_first_seen_s": (now_ms - p.first_seen_ms) / 1000.0,
            "reserves": {"quote_sol": q / LAMPORTS if q is not None else None, "v_sol": p.v / LAMPORTS,
                         "quote_eff_sol": (q + p.v) / LAMPORTS if q is not None else None}}


def prints_15m_short(events: Any, now_ms: int, need: int) -> bool:
    """hv4_runner.prints_15m_short (569-583 at 9ed4c5ff). SCAN SPEED ONLY."""
    cut60, cut15 = now_ms - 60 * MIN_MS, now_ms - 15 * MIN_MS
    n = 0
    for ev in reversed(events):
        t = ev[0]
        if t < cut60:
            break
        if t >= cut15:
            n += 1
            if n >= need:
                return False
    return True


TIMING = collections.Counter()  # wall seconds, stderr only (never in the hashed report)


class DayCounts:
    def __init__(self) -> None:
        self.scans = 0
        self.scans_state_stale = 0  # state clock more than 10 s behind T (every pool fails state_fresh)
        self.scans_no_filter_pass = 0
        self.pool_scans_wsol_pumpswap = 0
        self.pool_scans_prescreen_fail = 0
        self.pool_scans_filter_fail = 0
        self.pool_scans_filter_pass = 0
        self.v_missing = 0
        self.v_band = 0
        self.persona = {p: collections.Counter() for p in PERSONAS}
        self.tape_veto = {p: collections.Counter() for p in PERSONAS}


def scan_once(R: SimpleNamespace, st: Any, T: int, limiter: Any, dc: DayCounts, prescreen: bool = True) -> dict[str, Any]:
    """One scan at instant T. Returns the per-persona card counts of this scan (for the tests)."""
    C = R.C
    LAM = R.AS.LAMPORTS
    t0 = time.perf_counter()
    st.poll(until_ms=T - 1)
    TIMING["poll_s"] += time.perf_counter() - t0
    if st.now_ms >= T:
        raise AsOfError(f"state clock {st.now_ms} is not before the scan instant {T}")
    dc.scans += 1
    state_now = st.now_ms
    if T - state_now > C.STATE_LAG_MAX_MS:
        dc.scans_state_stale += 1
    slot = st.slot
    cand, tags, feat_of, snap_of, prints_of = [], [], {}, {}, {}
    for p in st.pools_wsol(lambda p: p.prog == "pumpswap"):
        dc.pool_scans_wsol_pumpswap += 1
        if prescreen:
            verdict = C.prescreen(lite_snapshot(p, state_now, LAM), as_of_ms=T)
            if verdict == C.PRESCREEN_GO and prints_15m_short(p.events, state_now, C.PRINTS_15M_MIN):
                verdict = C.PRESCREEN_FAIL
            if verdict == C.PRESCREEN_FAIL:
                dc.pool_scans_prescreen_fail += 1
                code = C.v_filter_code(lite_snapshot(p, state_now, LAM))
                if code == "v_missing":
                    dc.v_missing += 1
                elif code == "v_band":
                    dc.v_band += 1
                continue
        snap = st.snapshot(p.id, candles=False)
        try:
            fr = C.filters(snap, as_of_ms=T)
        except C.LeakageError as e:
            raise AsOfError(str(e)[:120]) from e
        if not fr.passed:
            dc.pool_scans_filter_fail += 1
            if "v_missing" in fr.failed:
                dc.v_missing += 1
            if "v_band" in fr.failed:
                dc.v_band += 1
            continue
        series = C.series_from_pool_candles(p.candles, state_now // MIN_MS, R.AS.CANDLE_MIN, LAM)
        prints = C.norm_prints(list(p.events))
        feats = C.features(snap, prints, as_of_ms=T, series=series)
        cand.append(p.id)
        feat_of[p.id], snap_of[p.id], prints_of[p.id] = feats, snap, prints
        tags.extend(C.setups(feats, pool_id=p.id, as_of_ms=T, limiter=limiter))
    dc.pool_scans_filter_pass += len(cand)
    if not cand:
        dc.scans_no_filter_pass += 1
    ranked = C.rank_setups(tags)
    per_persona = {pr: [t for t in ranked if t.persona == pr][:TOP_K_PER_PERSONA] for pr in PERSONAS}
    for pr in PERSONAS:
        for t in per_persona[pr]:
            limiter.record(t.pool_id, pr, T)
    tagged = {t.pool_id for ts in per_persona.values() for t in ts}
    board: list[str] = []
    if (T // 1000) % (BOARD_EVERY_MS // 1000) == SCAN_SECOND_MS // 1000:
        rest = sorted((p for p in cand if p not in tagged), key=lambda p: (-(feat_of[p].get("vol_60m") or 0.0), p))
        board = rest[:BOARD_SIZE]
    chain = {"as_of_ms": T - 1, "as_of_slot": slot, "mint_authority": None, "freeze_authority": None, "token_program": C.SPL_TOKEN_PROGRAM, "extensions": [],
             "mayhem": False}  # NEUTRAL: it asserts nothing about any mint (see the module docstring)

    def card_ok(pid: str, persona: str) -> bool:
        snap, prints, feats = snap_of[pid], prints_of[pid], feat_of[pid]
        p = st.pool(pid)
        snap["candles"] = st.candles(p)  # a card needs them (runner _card, 942-943)
        C.assert_as_of(snap, T, prints=prints, chain=chain, as_of_slot=slot)
        fr = C.filters(snap, as_of_ms=T)
        vr = C.code_vetoes(snap, prints, chain, as_of_ms=T, as_of_slot=slot, feats=feats)
        reasons = [r for r in (*fr.failed, *vr.reasons) if r not in CHAIN_ONLY_REASONS]
        if not C.is_safe_id(snap.get("id")):
            reasons.append("bad_pool_id")
        for r in reasons:
            dc.tape_veto[persona][r] += 1
        return not reasons

    out = {}
    for pr in PERSONAS:
        c = dc.persona[pr]
        c["scans"] += 1
        c["tags_top"] += len(per_persona[pr])
        n_tag = sum(1 for t in per_persona[pr] if card_ok(t.pool_id, pr))
        n_board = sum(1 for pid in board if card_ok(pid, pr))
        c["tag_cards_ok"] += n_tag
        c["board_cards_ok"] += n_board
        c["scans_with_card"] += int(n_tag + n_board > 0)
        out[pr] = (n_tag, n_board)
    return out


def scan_instants(start_ms: int, end_ms: int) -> list[int]:
    first = start_ms - start_ms % SCAN_EVERY_MS + SCAN_SECOND_MS
    if first < start_ms:
        first += SCAN_EVERY_MS
    return list(range(first, end_ms, SCAN_EVERY_MS))


def gap_counts(spans: dict, start_ms: int, end_ms: int) -> dict[str, Any]:
    """Counts per prog for the data_loss spans that overlap [start, end), and the union seconds clipped to it. No stamps of single spans are printed."""
    by_prog: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    n_all = collections.Counter()
    kinds = collections.Counter()
    for (prog, kind, fr), (to, loss) in spans.items():
        if to < start_ms or fr >= end_ms:
            continue
        n_all[prog] += 1
        if loss:
            by_prog[prog].append((max(fr, start_ms), min(max(to, fr), end_ms)))
            kinds[f"{prog}:{kind}"] += 1
    secs = {}
    for prog, iv in by_prog.items():
        iv.sort()
        tot, cur_s, cur_e = 0, None, None
        for a, b in iv:
            if cur_e is None or a > cur_e:
                if cur_e is not None:
                    tot += cur_e - cur_s
                cur_s, cur_e = a, b
            else:
                cur_e = max(cur_e, b)
        if cur_e is not None:
            tot += cur_e - cur_s
        secs[prog] = tot / 1000.0
    ps = secs.get("pumpswap", 0.0) + secs.get("all", 0.0)
    return {"spans_any": dict(sorted(n_all.items())), "data_loss_spans": dict(sorted(kinds.items())), "data_loss_s": dict(sorted(secs.items())),
            "pumpswap_data_loss_s_incl_all": ps, "pumpswap_data_loss_le_1h": ps <= DATA_LOSS_MAX_S}


def count_window(R: SimpleNamespace, state: Any, limiter: Any, label: str, start_ms: int, end_ms: int, prescreen: bool = True,
                 per_scan: list | None = None, progress: Any = None) -> tuple[dict[str, Any], DayCounts]:
    dc = DayCounts()
    for T in scan_instants(start_ms, end_ms):
        res = scan_once(R, state, T, limiter, dc, prescreen=prescreen)
        if per_scan is not None:
            per_scan.append((T, res))
        if progress is not None and dc.scans % 24 == 0:
            progress(label, dc.scans, T)
    gaps = gap_counts(state.spans, start_ms, end_ms)
    rec = {"kind": "day", "day": label, "status": "OK", "scans": dc.scans, "scans_state_stale": dc.scans_state_stale, "scans_no_filter_pass": dc.scans_no_filter_pass,
           "pool_scans_wsol_pumpswap": dc.pool_scans_wsol_pumpswap, "pool_scans_prescreen_fail": dc.pool_scans_prescreen_fail,
           "pool_scans_filter_fail": dc.pool_scans_filter_fail, "pool_scans_filter_pass": dc.pool_scans_filter_pass, "v_missing": dc.v_missing, "v_band": dc.v_band,
           "gaps": gaps, "state_bad_lines": state.counters["bad_lines"], "state_apply_errors": state.counters["apply_errors"]}
    return rec, dc


# ------------------------------------------------------------------------------------------------------------------------------ output
def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def arm_rows(label: str, dc: DayCounts) -> list[dict]:
    rows = []
    for arm in ARMS:
        pr, role = arm.split("-")
        c = dc.persona[pr]
        tag, board = c["tag_cards_ok"], c["board_cards_ok"]
        if role == "X":
            n, basis = tag, "tagged cards (X arms trade tags only; book gates not applied)"
        elif role == "R":
            n, basis = tag + board, "the learning arm's bound (R matches L's entries)"
        else:
            n, basis = tag + board, "tagged + board cards (model choice, risk grade not applied)"
        rows.append({"kind": "arm_day", "day": label, "arm": arm, "attempts_ub": n, "basis": basis})
    return rows


def build_report(header: dict, days: list[tuple[dict, DayCounts | None]]) -> list[dict]:
    out = [header]
    ok_days = [r["day"] for r, dc in days if r["status"] == "OK"]
    per_arm = collections.defaultdict(list)
    for rec, dc in days:
        out.append(rec)
        if dc is None:
            continue
        for pr in PERSONAS:
            c = dc.persona[pr]
            out.append({"kind": "persona_day", "day": rec["day"], "persona": pr, "scans": c["scans"], "tags_top": c["tags_top"], "tag_cards_ok": c["tag_cards_ok"],
                        "board_cards_ok": c["board_cards_ok"], "scans_with_card": c["scans_with_card"], "tape_veto": dict(sorted(dc.tape_veto[pr].items())),
                        "chain_veto_not_applied": c["tag_cards_ok"] + c["board_cards_ok"]})
        for r in arm_rows(rec["day"], dc):
            out.append(r)
            per_arm[r["arm"]].append(r["attempts_ub"])
    all_ok = len(ok_days) == len(days) and len(days) > 0
    floor_rows = []
    for arm in ARMS:
        v = per_arm.get(arm, [])
        avg = (sum(v) / len(v)) if v else 0.0
        floor_rows.append({"kind": "floor", "arm": arm, "days_ok": len(v), "total_ub": sum(v), "avg_per_day_ub": round(avg, 4), "any_day_zero": any(x == 0 for x in v) or not v,
                           "pass": bool(v) and avg >= FLOOR_PER_DAY and all(x > 0 for x in v)})
    out.extend(floor_rows)
    out.append({"kind": "verdict", "days_requested": len(days), "days_ok": len(ok_days), "all_days_ok": all_ok,
                "floor_pass_all_9_arms": bool(all_ok and all(f["pass"] for f in floor_rows)), "floor_per_day": FLOOR_PER_DAY, "disclosure": DISCLOSURE})
    return out


def render(rows: list[dict]) -> str:
    body = "".join(canon(r) + "\n" for r in rows)
    return body + "sha256 " + hashlib.sha256(body.encode()).hexdigest() + "\n"


# ------------------------------------------------------------------------------------------------------------------------------ main
def run(a: argparse.Namespace, R: SimpleNamespace, repo_sha: str, log: Any = sys.stderr) -> list[dict]:
    global _GAP_EXTRA
    _GAP_EXTRA = tuple(R.AS.GAP_EXTRA)
    State = make_state_class(R.AS)
    windows = []
    if a.probe_hour:
        h0 = int(datetime.strptime(a.probe_hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
        windows.append((a.probe_hour, h0, h0 + HOUR_MS))
    else:
        for d in a.days:
            s = parse_day(d)
            windows.append((d, s, s + DAY_MS))
    header = {"kind": "header", "tool": "hv4_p0_count", "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "repo_sha": repo_sha,
              "frozen_sha": PINNED_SHA, "windows": [w[0] for w in windows], "warmup_min": a.warmup_min, "scan_every_ms": SCAN_EVERY_MS, "scan_second_ms": SCAN_SECOND_MS,
              "top_k_per_persona": TOP_K_PER_PERSONA, "board_every_ms": BOARD_EVERY_MS, "board_size": BOARD_SIZE, "as_of": "poll(until_ms=T-1); assert state.now_ms < T",
              "chain_vetoes": "not applied (neutral chain; counted as chain_veto_not_applied)", "prescreen": not a.no_prescreen,
              "fields": "type,prog,t_recv_ms; trade: side,trader,mint,pool,quote_mint,virtual_quote_reserves,slot,sol_lamports,token_raw,quote_reserve,base_reserve,lp_fee,pool_quote_amount; "
                        "create/pool_create/pool_info/complete: ids,quote_mint,slot,reserves; gap: kind,span stamps,counts; hb: prog"}
    results: list[tuple[dict, DayCounts | None]] = []
    state = limiter = None
    prev_end = None
    t_start = time.time()

    def progress(label: str, n: int, T: int) -> None:
        print(f"# progress window={label} scans={n} wall_s={time.time() - t_start:.0f} rss_mb={rss_mb():.0f}", file=log, flush=True)

    for label, s, e in windows:
        why = tape_check(a.tape_dir, s, e, a.warmup_min)
        if why:
            results.append(({"kind": "day", "day": label, "status": "FAIL", "reason": why}, None))
            state = limiter = prev_end = None
            continue
        if state is None or prev_end != s:  # contiguous windows keep the state (its warm-up is already in); otherwise a fresh state with its own warm-up
            state = State(a.tape_dir, start_ms=s - a.warmup_min * MIN_MS)
            limiter = R.C.SetupLimiter()
        rec, dc = count_window(R, state, limiter, label, s, e, prescreen=not a.no_prescreen, progress=progress)
        results.append((rec, dc))
        prev_end = e
    print(f"# done wall_s={time.time() - t_start:.1f} poll_s={TIMING['poll_s']:.1f} rss_peak_mb={rss_mb(peak=True):.0f}", file=log, flush=True)
    return build_report(header, results)


def rss_mb(peak: bool = False) -> float:
    if peak:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    try:
        with open("/proc/self/status") as f:
            for ln in f:
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV4 P0 counts-only (DEC-031 Am.4 item 2). Imports the rule code from --repo (clean checkout at the pinned sha).")
    ap.add_argument("--repo", required=True, help="clean checkout at the frozen sha; tools.hv4_cards / arena_state / arena_zst are imported from it")
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--days", default="", help="comma-separated UTC days YYYY-MM-DD")
    ap.add_argument("--warmup-min", type=int, default=365)
    ap.add_argument("--out", default=None, help="also write the report here (it is printed to stdout either way)")
    ap.add_argument("--expect-sha", default=PINNED_SHA, help="TEST ONLY; the job never passes it. The header records repo_sha")
    ap.add_argument("--probe-hour", default=None, help="TIMING PROBE: count only this closed UTC hour (YYYY-MM-DDTHH), warm-up before it. Not a P0 result")
    ap.add_argument("--no-prescreen", action="store_true", help="run the full filters on every WSOL PumpSwap pool (slower; the equivalence check)")
    a = ap.parse_args(argv)
    a.days = [d for d in a.days.split(",") if d]
    if not a.probe_hour and not a.days:
        print("hv4_p0_count: --days (or --probe-hour) is required", file=sys.stderr)
        return 2
    try:
        for d in a.days:
            parse_day(d)
        if a.warmup_min < 1:
            raise ValueError("warmup")
    except ValueError:
        print("hv4_p0_count: bad --days or --warmup-min", file=sys.stderr)
        return 2
    try:
        sha = verify_repo(a.repo, a.expect_sha)
        R = load_rules(a.repo)
    except (RepoError, OSError, subprocess.SubprocessError, ImportError) as e:
        print(f"hv4_p0_count: refused: {e}", file=sys.stderr)
        return 2
    rows = run(a, R, sha)
    try:
        verify_repo(a.repo, a.expect_sha)  # the run must not have changed the checkout
    except RepoError as e:
        print(f"hv4_p0_count: refused after run: {e}", file=sys.stderr)
        return 2
    text = render(rows)
    sys.stdout.write(text)
    if a.out:
        Path(a.out).write_text(text)
    return 1 if any(r.get("kind") == "day" and r.get("status") != "OK" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
