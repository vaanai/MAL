#!/usr/bin/env python3
"""floor_p0_count: counts-only P0 count for FLOOR (bot `l1-floor-v2`, lib-c-L1 FLOOR-OPTION v2) under DEC-031 Amendment 3 item 1 and
Amendment 4 item 2 (QP-RULING-1021-floor.md C2/C3). Written to the pattern of tools/hv4_p0_count.py (PR #803). NOT a host replay: no ArenaEngine, no ledger, no registry, no
RPC, no price, no fill, no exit, no outcome.

WHAT IT CALLS. From the clean checkout `--repo` at the registration-candidate sha it imports tools.arena_pool_table.PoolTable and tools.arena_rule_l1 (the frozen rule), builds the
table the way HostV2 does (mode = rule.table_mode, "complete"), and for every tape row in arrival order does exactly what HostV2._on_row does before the clock check:
    table.update(row)                      (every row, from the first tape hour on, so pool age / 24 h volume / 6 h spot exist)
    rule.on_row(ctx, row)                  (only rows whose t_recv_ms falls in a selected UTC day; ctx.positions is EMPTY, ctx.state.pool() is None, ctx.table is the table)
A returned Buy is one SIGNAL (the entry predicate of arena_rule_l1.on_row: WSOL quote on the row first, V band, age 24-200 h, post-print real quote <= 0.5 SOL, previous print
within 6 h, 24 h volume >= 1 SOL, 6 h spot ratio < 0.80). The state-dependent parts of the live book are NOT applied: the one open position per pool and the 60 s re-entry cooldown
depend on exits, which depend on prices. They are bounded instead, from decision times only:
    att_lo  signals thinned by a per-pool lock of HOLD_MS + COOLDOWN_MS (the position is held to the 24 h time exit; no take profit)   -> LOWER bound of would-be attempts
    att_hi  signals thinned by a per-pool lock of COOLDOWN_MS + LEG_MS (every position exits as early as the leg latency allows)      -> UPPER bound
Real attempts lie between. K1: the rule is run (and signals collected) from min(days) - (HOLD_MS + COOLDOWN_MS) through the end of max(days), unselected days in between included, so a signal
before or between selected days locks its pool exactly as the live book would; att_lo/att_hi are counted on selected days only. att_lo is therefore the same whichever days are selected.
WHAT att_lo DOES NOT MODEL: it is a lower bound up to the 1 s exit tick and sell-retry delay; live stale-row drops, buy refusals and failed buys are not modelled (the last two raise real
attempts above att_lo). Take profit is ignored (it can only free a pool earlier). Fewer than 3 days: floor false, days_lt_3 true. Corrupt/torn tape lines are counted per day (bad_lines) and fail that day.
meta fields that need prices (mark0) are None here; nothing in the signal depends on them.

TAPE FIELDS. Every row is projected to a whitelist the moment it is read: type, prog, venue, t_recv_ms, slot, event_ts, mint, pool, side, quote_mint, virtual_quote_reserves,
quote_reserve, base_reserve, sol_lamports, token_raw (trade); mint, is_mayhem_mode (create); mint, event_ts (complete); gap: kind, prog, from_ms, paused_since_ms, to_ms,
data_loss. signature and event_index are kept as the table's de-duplication key and the rule's meta field, never printed; the key is inert in this path (no checkpoint load). trader, creator,
name, symbol are never kept. No mint, pool, wallet or signature is printed.

OUTPUT. Canonical JSON lines, then a last line `sha256 <hex>` over everything before it: header (repo_sha, tool_sha256, rule/table file sha256, constants), one `day` line per selected
day (status, rows dispatched, signals, att_lo, att_hi, gap counts), one `verdict` line (floor on att_lo: mean >= 2.0 and no day 0; rate; H). Timing and memory go to stderr.
Exit 0 ok, 1 a day FAILED (a tape hour missing between table start and the end of that day), 2 tool error / repo not pinned / dirty.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PINNED_SHA = "5e12f8f597571d6d5191a7ba38125c67ad723b7d"
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
FLOOR_PER_DAY = 2.0  # Am.4 item 2.1
DATA_LOSS_MAX_S = 3600  # Am.3 item 1 gap rule, reported as a flag (the A1 job and the A2 summary decide)
DISCLOSURE = ("P0 for FLOOR is a structure-only entry-predicate count of the frozen rule module; the one-position lock and cooldown are bounded, not simulated: att_lo (24 h lock, no take profit) "
              "decides the floor and H, att_hi is reported. The pool universe is still ramping (pools must complete on the tape and age 24 h; tape starts 2026-10-19T08:05Z), which "
              "understates the rate (QP-RULING-1021-floor C2).")
KEEP = {
    "trade": ("prog", "venue", "slot", "event_ts", "mint", "pool", "side", "quote_mint", "virtual_quote_reserves", "quote_reserve", "base_reserve", "sol_lamports", "token_raw",
              "signature", "event_index"),
    "create": ("mint", "is_mayhem_mode", "signature", "event_index"),
    "complete": ("mint", "event_ts", "signature", "event_index"),
    "gap": ("kind", "prog", "from_ms", "paused_since_ms", "to_ms", "data_loss"),
}


class RepoError(RuntimeError):
    pass


def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RepoError("git " + " ".join(args) + " failed")
    return r.stdout


def verify_repo(repo: str, expect_sha: str) -> str:
    head = _git(repo, "rev-parse", "HEAD").strip()
    if head != expect_sha:
        raise RepoError(f"repo HEAD {head[:12]} is not the pinned sha {expect_sha[:12]}")
    if _git(repo, "status", "--porcelain").strip():
        raise RepoError("repo is dirty (git status --porcelain is not empty)")
    return head


def load_modules(repo: str) -> SimpleNamespace:
    sys.dont_write_bytecode = True
    repo = str(Path(repo).resolve())
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p and str(Path(p).resolve()) != here]
    for m in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
        del sys.modules[m]
    sys.path.insert(0, repo)
    from tools import arena_pool_table as PT  # noqa: PLC0415
    from tools import arena_rule_l1 as L1  # noqa: PLC0415

    for mod in (PT, L1):
        if not str(Path(mod.__file__).resolve()).startswith(repo + os.sep):
            raise RepoError(f"{mod.__name__} was not imported from {repo}")
    return SimpleNamespace(PT=PT, L1=L1, repo=repo)


class _State:
    def pool(self, _pool: Any) -> None:
        return None


class _Ctx:
    """What arena_rule_l1.on_row reads: positions (empty), state.pool (None), table, now_ms."""

    positions: tuple = ()
    attempted: frozenset = frozenset()

    def __init__(self, table: Any) -> None:
        self.state, self.table, self.now_ms, self.clock_start_ms = _State(), table, 0, 0


def project(row: dict) -> dict | None:
    typ = row.get("type")
    t = row.get("t_recv_ms")
    if typ not in KEEP or isinstance(t, bool) or not isinstance(t, int):
        return None
    out = {"type": typ, "t_recv_ms": t}
    for k in KEEP[typ]:
        if k in row:
            out[k] = row[k]
    return out


def hour_day(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def hour_key(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def parse_day(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def parse_hour(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def tape_files(tape_dir: str) -> list[tuple[int, Path]]:
    files: dict[int, Path] = {}
    for f in Path(tape_dir).iterdir():
        n = f.name
        if n.startswith("tape-") and (n.endswith(".jsonl") or n.endswith(".jsonl.zst")):
            try:
                h = parse_hour(n[5:18])
            except ValueError:
                continue
            if h not in files or n.endswith(".jsonl"):
                files[h] = f
    return sorted(files.items())


def lines(path: Path):
    if path.name.endswith(".zst"):
        z = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert z.stdout is not None
        try:
            yield from z.stdout
        finally:
            z.stdout.close()
            if z.wait() != 0:
                raise RuntimeError("zstd failed")
    else:
        with open(path, "rb") as fh:
            yield from fh


def thin(times: list[int], lock_ms: int) -> list[int]:
    """Per-pool lock thinning on signal times (ms); times sorted."""
    out, until = [], -1
    for t in times:
        if t >= until:
            out.append(t)
            until = t + lock_ms
    return out


def gap_counts(spans: dict, start_ms: int, end_ms: int) -> dict[str, Any]:
    by_prog: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    n_all, kinds = collections.Counter(), collections.Counter()
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
        tot, cs, ce = 0, None, None
        for a, b in iv:
            if ce is None or a > ce:
                if ce is not None:
                    tot += ce - cs
                cs, ce = a, b
            else:
                ce = max(ce, b)
        if ce is not None:
            tot += ce - cs
        secs[prog] = tot / 1000.0
    ps = secs.get("pumpswap", 0.0) + secs.get("all", 0.0)
    return {"spans_any": dict(sorted(n_all.items())), "data_loss_spans": dict(sorted(kinds.items())), "data_loss_s": dict(sorted(secs.items())),
            "pumpswap_data_loss_s_incl_all": ps, "pumpswap_data_loss_le_1h": ps <= DATA_LOSS_MAX_S}


def canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha_file(p: str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(a: argparse.Namespace, M: SimpleNamespace, sha: str) -> list[dict]:
    PT, L1 = M.PT, M.L1
    rule = L1.make_rule()
    table = PT.PoolTable(rule.table_mode)
    ctx = _Ctx(table)
    days = sorted(a.days)
    day_ms = {d: parse_day(d) for d in days}
    win = lambda t: next((d for d in days if day_ms[d] <= t < day_ms[d] + DAY_MS), None)  # noqa: E731
    files = tape_files(a.tape_dir)
    if not files:
        raise RuntimeError("no tape files")
    first_h = files[0][0] if not a.table_from else parse_hour(a.table_from)
    end_ms = max(day_ms.values()) + DAY_MS
    have = {h for h, _ in files}
    missing = [h for h in range(first_h, end_ms, HOUR_MS) if h not in have]
    lock_from = max(first_h, min(day_ms.values()) - (L1.HOLD_MS + L1.COOLDOWN_MS))
    allsig: dict[str, list[int]] = collections.defaultdict(list)  # pool -> signal times (every dispatched day, selected or not)
    rows_disp = collections.Counter()
    bad_lines = collections.Counter()
    spans: dict[tuple, list] = {}
    t_start = time.time()
    n_rows = 0
    for h, f in files:
        if h < first_h or h >= end_ms:
            continue
        for ln in lines(f):
            if not ln.strip():
                continue
            try:
                raw = json.loads(ln)
            except ValueError:
                bad_lines[hour_day(h)] += 1  # K3: counted, fails the day
                continue
            row = project(raw) if isinstance(raw, dict) else None
            if row is None:
                continue  # K2: rows are parsed, never grepped; table.update itself ignores a pump.fun trade
            t = row["t_recv_ms"]
            if row["type"] == "gap":
                prog, kind = str(row.get("prog") or "all"), row.get("kind")
                fr = next((row[k] for k in ("from_ms", "paused_since_ms") if isinstance(row.get(k), int)), t)
                to = row["to_ms"] if isinstance(row.get("to_ms"), int) else t
                sp = spans.setdefault((prog, kind, fr), [max(fr, to), False])
                sp[0] = max(sp[0], to)
                sp[1] = sp[1] or bool(row.get("data_loss"))
                continue
            table.update(row)  # HostV2._on_row: the table sees every row first
            if row["type"] != "trade" or t < lock_from or t >= end_ms:
                continue
            d = win(t)
            ctx.now_ms = t
            if d is not None:
                rows_disp[d] += 1
            try:
                orders = rule.on_row(ctx, row)
            except Exception:  # noqa: BLE001 - counted, never swallowed silently: a rule exception FAILS every selected day
                rows_disp["error"] += 1
                continue
            if any(type(o).__name__ == "Buy" for o in orders):
                allsig[row["pool"]].append(t)
            n_rows += 1
        print(f"# hour {hour_key(h)} rows_dispatched={n_rows} wall_s={time.time() - t_start:.0f} rss_mb={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}", file=sys.stderr, flush=True)
    # thinning over the selected days in time order, per pool, locks carried across days
    lo_t, hi_t, sig_t, carried = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    for pool, ts in allsig.items():
        ts.sort()
        for t in ts:
            if win(t) is not None:
                sig_t[win(t)] += 1
        for t in thin(ts, L1.HOLD_MS + L1.COOLDOWN_MS):
            if win(t) is not None:
                lo_t[win(t)] += 1
        for t in thin(ts, L1.COOLDOWN_MS + L1.LEG_MS):
            if win(t) is not None:
                hi_t[win(t)] += 1
        for d in days:  # signals before the day (inside the lock horizon) that were carried into it
            carried[d] += sum(1 for t in ts if day_ms[d] - (L1.HOLD_MS + L1.COOLDOWN_MS) <= t < day_ms[d])
    rows: list[dict] = [{"kind": "header", "tool": "floor_p0_count", "repo_sha": sha, "tool_sha256": sha_file(__file__),
                         "rule_sha256": sha_file(str(Path(M.repo) / "tools/arena_rule_l1.py")), "table_sha256": sha_file(str(Path(M.repo) / "tools/arena_pool_table.py")),
                         "bot_id": L1.BOT_ID, "table_mode": rule.table_mode, "windows": days, "table_from": hour_key(first_h), "lock_warmup_from": hour_key(lock_from),
                         "constants": {"HOLD_MS": L1.HOLD_MS, "COOLDOWN_MS": L1.COOLDOWN_MS, "LEG_MS": L1.LEG_MS, "ATT_HI_LOCK_MS": L1.COOLDOWN_MS + L1.LEG_MS,
                                       "ATT_LO_LOCK_MS": L1.HOLD_MS + L1.COOLDOWN_MS}, "fields": sorted({k for v in KEEP.values() for k in v}),
                         "not_read": "trader, creator, name, symbol; signature/event_index only as the table's de-dup key, never printed"}]
    ok_all = True
    for d in days:
        s, e = day_ms[d], day_ms[d] + DAY_MS
        miss = [m for m in missing if m < e]  # any tape hour missing from table start to the end of the day
        err, bad = rows_disp["error"], bad_lines[d]
        status = "OK" if not miss and not err and not bad else "FAILED"
        ok_all &= status == "OK"
        rows.append({"kind": "day", "day": d, "status": status, "reason": None if status == "OK" else ("tape_missing_hours=%d" % len(miss) if miss else "rule_errors=%d" % err if err else "bad_lines=%d" % bad),
                     "rows_dispatched": rows_disp[d], "signals": sig_t[d], "bad_lines": bad, "carried_lock_signals": carried[d], "att_lo": lo_t[d], "att_hi": hi_t[d], "gaps": gap_counts(spans, s, e)})
    lo = [lo_t[d] for d in days]
    hi = [hi_t[d] for d in days]
    avg_lo, avg_hi = sum(lo) / len(lo), sum(hi) / len(hi)
    lt3 = len(days) < 3
    floor = (not lt3) and ok_all and avg_lo >= FLOOR_PER_DAY and all(x > 0 for x in lo)
    H = lambda r: min(90, max(14, math.ceil(150 / r))) if r > 0 else None  # noqa: E731
    rows.append({"kind": "verdict", "days": days, "att_lo_per_day": lo, "att_hi_per_day": hi, "avg_att_lo": avg_lo, "avg_att_hi": avg_hi, "any_day_zero_att_lo": any(x == 0 for x in lo),
                 "floor_pass_att_lo": floor, "days_lt_3": lt3, "rate_att_lo": avg_lo, "H_days_att_lo": H(avg_lo) if floor else None, "H_days_att_hi_report_only": H(avg_hi),
                 "all_days_ok": ok_all, "disclosure": DISCLOSURE})
    return rows


def render(rows: list[dict]) -> str:
    body = "".join(canon(r) + "\n" for r in rows)
    return body + canon({"kind": "sha256", "sha256": hashlib.sha256(body.encode()).hexdigest()}) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FLOOR P0 counts-only (DEC-031 Am.3 item 1 / Am.4 item 2). Imports the rule from --repo (clean checkout at the pinned sha).")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--days", required=True, help="comma-separated UTC days YYYY-MM-DD")
    ap.add_argument("--table-from", default=None, help="UTC hour YYYY-MM-DDTHH where the table starts (default: the first tape hour)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--expect-sha", default=PINNED_SHA, help="TEST ONLY (needs MAL_ARENA_TEST=1)")
    a = ap.parse_args(argv)
    a.days = [d for d in a.days.split(",") if d]
    try:
        for d in a.days:
            parse_day(d)
        if not a.days:
            raise ValueError("days")
        if a.table_from:
            parse_hour(a.table_from)
    except ValueError:
        print("floor_p0_count: bad --days or --table-from", file=sys.stderr)
        return 2
    if a.expect_sha != PINNED_SHA and os.environ.get("MAL_ARENA_TEST") != "1":
        print("floor_p0_count: refused: --expect-sha other than the pinned sha needs MAL_ARENA_TEST=1", file=sys.stderr)
        return 2
    try:
        sha = verify_repo(a.repo, a.expect_sha)
        M = load_modules(a.repo)
    except (RepoError, OSError, subprocess.SubprocessError, ImportError) as e:
        print(f"floor_p0_count: refused: {e}", file=sys.stderr)
        return 2
    try:
        rows = run(a, M, sha)
    except Exception as e:  # noqa: BLE001
        print(f"floor_p0_count: error: {type(e).__name__}", file=sys.stderr)
        return 2
    try:
        verify_repo(a.repo, a.expect_sha)
    except RepoError as e:
        print(f"floor_p0_count: refused after run: {e}", file=sys.stderr)
        return 2
    text = render(rows)
    sys.stdout.write(text)
    if a.out:
        Path(a.out).write_text(text)
    return 1 if any(r.get("kind") == "day" and r.get("status") != "OK" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
