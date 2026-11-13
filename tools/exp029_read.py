"""EXP-029 read tool: the floor-lane-1 gate read (EXP/EXP-029-floor-lane-1-gate-read-prereg.md, §1-§6, Errata 1, 1a-1g).

One look at R. Run once by the manager as a MiScusi job on mal-fast-0 (sudo -n, the lane ledger is root-only); no agent runs it on real files. Built and tested on
synthetic ledgers only.

    python -m tools.exp029_read --lane-ledger-root DIR --host-decisions-root DIR [--bridge-log PATH] --disclosures FILE --out-json PATH --out-md PATH

The result is FAIL (procedural) whatever the four tests give (Erratum 1g rules 1-2). The four section-4 tests run on the trades in [S, C), pooled and split by
stake, and are reported as descriptive numbers ("holds: yes/no", "value vs threshold"). The report never calls them a pass and never calls the FAIL a
verdict on the book.

Read-only on its inputs: files are opened O_RDONLY, no lock is taken (the executor is live), a torn last line is ignored and counted, and any output path that
resolves inside an input root is refused. The mint is used inside the tool to group rows and is never written to an output.

Ledger layout (code only): `<lane-ledger-root>/lane-ledger.jsonl`, one JSON row per line (tools/h5_lane_executor.py LaneLedger._write_line; the name is
tools/lane_daily_report.py LEDGER_NAME). Per-bot backlog files `lane-ledger.jsonl.backlog.<bot>` hold rows not yet drained into it. Bridge log: rows
`{"kind": "entry_refused", "why": ...}` (tools/arena_live_bridge.py:1066; `lane_full` is the ledger label floor_lane_executor.LANE_REASON gives `lane_max_open`).
Host decisions: `<root>/decisions/l1-floor-v2/<day>.jsonl`, rows with `kind` = `start` carry `frozen_sha`, `binding.host_sha256` and `ms`
(tools/arena_rules.py:488, tools/floor_lane_host.py lane_binding).
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _ms(iso: str) -> int:
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) * 1000 + (int(iso[20:23]) if len(iso) > 20 else 0)


S_MS = _ms("2026-11-09T06:00:00.000Z")  # EXP-029 s2: S = 2026-11-09T06:00:00Z
EVENT_MS = _ms("2026-11-12T00:46:04.584Z")  # Erratum 1g rule 1: the 0.10 SOL config install instant
C_MS = _ms("2026-11-13T00:00:00.000Z")  # s3 + Erratum 1g: C = the first 00:00Z after the event
R_MS = C_MS + 25 * HOUR_MS  # s3: R = C + 25 h = 2026-11-14T01:00:00Z
BOT_ID = "floor-lane-1"  # s1: LANE_BOT_ID (tools/floor_lane_executor.py:44)
ARM = "l1-floor-v2"  # s1: the book arm; the host's decision-log directory name
STAKES = {30_000_000: "0.03", 100_000_000: "0.10"}  # s1 lane parameter and Erratum 1g: the two stakes of the split
LEDGER_NAME = "lane-ledger.jsonl"  # tools/lane_daily_report.py LEDGER_NAME
BACKLOG_PREFIX = LEDGER_NAME + ".backlog."  # tools/h5_lane_executor.py LaneLedger.backlog_path
N_MIN, DAYS_MIN = 100, 5  # s4 tests 1 and 2
BOOT_DRAWS, BOOT_SEED, BOOT_PCT = 1000, 1, 5  # s4 test 3
TOP_REMOVED = 3  # s4 test 4
RESULT = "FAIL (procedural, Erratum 1g)"
HEADER = f"EXP-029 read at R = 2026-11-14T01:00:00Z: {RESULT}"
TEST_NOW_ENV = "EXP029_READ_TEST_NOW"  # --now-ms is refused unless the tests set this
LABEL = "descriptive"


class ReadError(RuntimeError):
    """A refusal: the read does not run (or does not finish) and leaves no marker."""


def iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"


def day_of(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms // 1000))


def sol(lamports: int) -> str:
    sign = "-" if lamports < 0 else ""
    a = abs(int(lamports))
    return f"{sign}{a // 1_000_000_000}.{a % 1_000_000_000:09d}"


# ---------------------------------------------------------------- reading

def read_bytes(path: Path) -> bytes:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def parse_jsonl(data: bytes, what: str) -> tuple[list[dict[str, Any]], int]:
    """(rows, torn). A last line that does not parse is a torn write by the live writer: ignored and counted. An unreadable line anywhere else refuses."""
    lines = data.split(b"\n")
    rows: list[dict[str, Any]] = []
    torn = 0
    last = max((i for i, ln in enumerate(lines) if ln.strip()), default=-1)
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            if i == last:
                torn += 1
                continue
            raise ReadError(f"{what}: unreadable line {i + 1} (not the last line)") from None
        if not isinstance(row, dict):
            raise ReadError(f"{what}: line {i + 1} is not an object")
        rows.append(row)
    return rows, torn


def _int(r: dict[str, Any], key: str) -> int:
    v = r.get(key, 0)
    if v is None:
        v = 0
    if isinstance(v, bool) or not isinstance(v, int):
        raise ReadError(f"ledger row field {key!r} is not an integer")
    return v


def load_ledger(root: Path) -> tuple[list[dict[str, Any]], int]:
    path = root / LEDGER_NAME
    if not path.is_file():
        raise ReadError(f"lane ledger missing: {LEDGER_NAME} not found under --lane-ledger-root")
    for name in sorted(os.listdir(root)):
        if name.startswith(BACKLOG_PREFIX) and os.stat(root / name).st_size > 0:
            raise ReadError(f"a lane backlog file is not empty ({name}): rows not yet in the ledger are missing")
    rows, torn = parse_jsonl(read_bytes(path), LEDGER_NAME)
    seen: set[str] = set()
    out = []
    for r in rows:  # a backlog row appended twice by a crash is one row (lane_fold does the same)
        rid = r.get("row_id")
        if rid is not None:
            if rid in seen:
                continue
            seen.add(rid)
        out.append(r)
    if not out:
        raise ReadError("lane ledger has no rows")
    return out, torn


# ---------------------------------------------------------------- trades

def build_trades(rows: list[dict[str, Any]], cut_ms: int) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Every trade candidate, by bot_id, in file order (EXP-029 s2). A candidate is one send_intent with the sum of its rows through the first closing row.
    Rows with ts_ms >= cut_ms (R) are ignored: a trade whose closing row is at or after R is open at R. Returns (candidates by bot, rows ignored for the cut)."""
    by_bot: dict[str, list[dict[str, Any]]] = {}
    cur: dict[tuple[str, str], dict[str, Any]] = {}
    cut = 0
    for r in rows:
        ev = r.get("ev")
        if not isinstance(ev, str):
            raise ReadError("ledger row has no `ev`")
        if ev in ("start", "snap", "would_send") or r.get("mode") == "dry":
            continue  # snap rows carry no trade; dry rows are never trades
        bot, mint = r.get("bot_id"), r.get("mint")
        if not isinstance(bot, str) or not isinstance(mint, str):
            raise ReadError("ledger row has no bot_id / mint")
        ts = _int(r, "ts_ms")
        if ts >= cut_ms:
            cut += 1
            continue
        key = (bot, mint)
        net = _int(r, "sol_in_lamports") - _int(r, "sol_out_lamports")
        if ev == "send_intent":
            sig = r.get("signature")
            order = sig[len("intent:"):] if isinstance(sig, str) and sig.startswith("intent:") else f"row:{r.get('row_id')}"
            t = {"bot": bot, "mint": mint, "ts_ms": ts, "order": order, "stake": _int(r, "stake_lamports"), "pnl": net, "closed": None, "not_sent": False,
                 "est_rows": 1 if r.get("estimated") is True else 0, "close_ts": None}
            by_bot.setdefault(bot, []).append(t)
            cur[key] = t
            continue
        t = cur.get(key)
        if t is None or t["closed"] is not None:
            continue  # no open trade for this bot and mint: the row belongs to no trade
        t["pnl"] += net
        t["est_rows"] += 1 if r.get("estimated") is True else 0
        if ev in ("exit", "writeoff") or (ev == "fill" and r.get("ok") is not True):
            t["closed"] = ev if ev != "fill" else "fill_error"
            t["close_ts"] = ts
            t["not_sent"] = ev == "writeoff" and r.get("reason") == "not_sent"
    return by_bot, cut


# ---------------------------------------------------------------- tests (s4)

def bootstrap_bound(x: np.ndarray) -> float:
    n = len(x)
    rng = np.random.default_rng(BOOT_SEED)
    idx = rng.integers(0, n, size=(BOOT_DRAWS, n))
    means = x[idx].mean(axis=1)
    return float(np.percentile(means, BOOT_PCT))


def run_tests(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """The four s4 tests on one set of trades (already sorted by (ts_ms, mint))."""
    n = len(trades)
    out: dict[str, Any] = {"label": LABEL, "n": n}
    if n == 0:
        out["note"] = "no trades"
        return out
    pnl = [t["pnl"] for t in trades]
    days: dict[str, int] = {}
    for t in trades:
        days[t["day"]] = days.get(t["day"], 0) + t["pnl"]
    pos = sum(1 for v in days.values() if v > 0)
    x = np.array(pnl, dtype=np.float64)
    bound = bootstrap_bound(x)
    out["t1_n"] = {"value": n, "threshold": N_MIN, "holds": n >= N_MIN}
    out["t2_days"] = {"distinct_days": len(days), "positive_days": pos, "days_threshold": DAYS_MIN, "rule": "positive days strictly more than half",
                      "holds": len(days) >= DAYS_MIN and 2 * pos > len(days), "day_totals_lamports": dict(sorted(days.items()))}
    out["t3_bound"] = {"lower_90_bound_mean_lamports": bound, "threshold_lamports": 0, "holds": bound > 0, "draws": BOOT_DRAWS, "seed": BOOT_SEED}
    if n <= TOP_REMOVED:
        out["t4_top3"] = {"note": "not defined for n <= 3", "holds": False}
    else:
        rest = sum(sorted(pnl, reverse=True)[TOP_REMOVED:])
        out["t4_top3"] = {"total_after_removing_top3_lamports": rest, "threshold_lamports": 0, "holds": rest > 0}
    out["all_four_hold"] = bool(out["t1_n"]["holds"] and out["t2_days"]["holds"] and out["t3_bound"]["holds"] and out["t4_top3"]["holds"])
    return out


# ---------------------------------------------------------------- bridge log and host

def read_bridge(path: Path, ledger_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows, torn = parse_jsonl(read_bytes(path), "bridge log")
    refused = [r for r in rows if r.get("kind") == "entry_refused" and r.get("mode", "live") == "live" and isinstance(r.get("ts_ms"), int)]
    labels: dict[str, int] = {}
    full: list[int] = []
    for r in refused:
        ts = r["ts_ms"]
        if S_MS <= ts < R_MS:
            lab = str(r.get("why"))[:60]
            labels[lab] = labels.get(lab, 0) + 1
            if r.get("why") == "lane_full" and ts < C_MS:
                full.append(ts)
    groups = {"i_other_lane_bot_holds_slot": 0, "ii_only_pre_S_floor_lane_1_holds_slot": 0, "iii_only_in_read_floor_lane_1_positions": 0}
    full.sort()
    openpos: dict[tuple[str, str], dict[str, Any]] = {}
    ptr = 0
    ledger = [r for r in ledger_rows if r.get("ev") not in ("start", "snap", "would_send") and r.get("mode") != "dry"]
    for ts in full:
        while ptr < len(ledger) and isinstance(ledger[ptr].get("ts_ms"), int) and ledger[ptr]["ts_ms"] <= ts:
            r = ledger[ptr]
            ptr += 1
            k = (r.get("bot_id"), r.get("mint"))
            ev = r.get("ev")
            if ev == "send_intent":
                openpos[k] = {"ts": r["ts_ms"]}
            elif ev == "fill" and r.get("ok") is True:
                pass
            elif ev in ("exit", "writeoff") or ev == "fill":
                openpos.pop(k, None)
        if any(k[0] != BOT_ID for k in openpos):
            groups["i_other_lane_bot_holds_slot"] += 1
        elif any(p["ts"] < S_MS for p in openpos.values()):
            groups["ii_only_pre_S_floor_lane_1_holds_slot"] += 1
        else:
            groups["iii_only_in_read_floor_lane_1_positions"] += 1
    latches = sorted(({"latch": str(r.get("latch"))[:40], "instant_utc": iso(r["ts_ms"])} for r in rows
                      if r.get("kind") == "latch" and r.get("mode", "live") == "live" and isinstance(r.get("ts_ms"), int)), key=lambda d: (d["instant_utc"], d["latch"]))
    return {"torn_lines_ignored": torn, "lane_full_in_S_C": len(full), "lane_full_groups": groups, "entry_refused_by_label_S_R": dict(sorted(labels.items())), "latch_rows": latches}


def read_host_starts(root: Path) -> list[dict[str, Any]]:
    d = root / "decisions" / ARM
    if not d.is_dir():
        raise ReadError(f"host decision log missing: decisions/{ARM} not found under --host-decisions-root")
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl"):
            continue
        rows, _ = parse_jsonl(read_bytes(d / name), f"decision log {name}")
        for r in rows:
            if r.get("kind") == "start":  # only the start rows are kept; no other decision row is used
                b = r.get("binding") if isinstance(r.get("binding"), dict) else {}
                out.append({"instant_utc": iso(int(r["ms"])), "frozen_sha": r.get("frozen_sha"), "host_sha256": b.get("host_sha256")})
    if not out:
        raise ReadError("host decision log has no start row")
    return out


# ---------------------------------------------------------------- report

def assemble(ledger_rows: list[dict[str, Any]], torn: int, starts: list[dict[str, Any]], bridge: dict[str, Any] | None, disclosures: str, now_ms: int) -> dict[str, Any]:
    cands, cut = build_trades(ledger_rows, R_MS)
    if not cands.get(BOT_ID):
        raise ReadError(f"no {BOT_ID} send_intent row in the ledger")
    mine = cands[BOT_ID]
    for t in mine:
        t["day"] = day_of(t["ts_ms"])
    live = [t for t in mine if not t["not_sent"]]
    trades = sorted((t for t in live if S_MS <= t["ts_ms"] < C_MS), key=lambda t: (t["ts_ms"], t["mint"]))
    pre = [t for t in live if t["ts_ms"] < S_MS]
    post = [t for t in live if C_MS <= t["ts_ms"] < R_MS]
    split: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        split.setdefault(STAKES.get(t["stake"], "other"), []).append(t)
    tests = {"pooled": run_tests(trades)}
    for k in ("0.03", "0.10"):
        tests[f"stake_{k}_sol"] = run_tests(split.get(k, []))
    if "other" in split:
        tests["stake_other"] = run_tests(split["other"])
    day_tot: dict[str, dict[str, int]] = {}
    for t in trades:
        d = day_tot.setdefault(t["day"], {"n": 0, "total_lamports": 0})
        d["n"] += 1
        d["total_lamports"] += t["pnl"]
    rows_out = [{"order": t["order"], "send_utc": iso(t["ts_ms"]), "trade_day": t["day"], "stake_lamports": t["stake"], "pnl_lamports": t["pnl"], "pnl_sol": sol(t["pnl"]),
                 "closing_row": t["closed"] or "none", "open_at_R": t["closed"] is None, "estimated": t["est_rows"] > 0} for t in trades]
    bots = []
    for bot in sorted(cands):
        sends = [t for t in cands[bot] if S_MS <= t["ts_ms"] < R_MS]
        if not sends:
            continue
        inr = [t for t in sends if t["ts_ms"] < C_MS and not t["not_sent"]]
        bots.append({"bot_id": bot, "send_intents_S_R": len(sends), "not_sent_S_R": sum(1 for t in sends if t["not_sent"]), "trades_S_C": len(inr),
                     "total_lamports_S_C": sum(t["pnl"] for t in inr), "sends_C_R": sum(1 for t in sends if t["ts_ms"] >= C_MS and not t["not_sent"]),
                     "result": RESULT if bot == BOT_ID else "no registered result in this read; listed per EXP-029 s6"})
    allm = cands[BOT_ID]
    counts = {
        "not_sent_pre_S": sum(1 for t in allm if t["not_sent"] and t["ts_ms"] < S_MS),
        "not_sent_S_C": sum(1 for t in allm if t["not_sent"] and S_MS <= t["ts_ms"] < C_MS),
        "not_sent_C_R": sum(1 for t in allm if t["not_sent"] and C_MS <= t["ts_ms"] < R_MS),
        "zero_cash_trades_S_C": sum(1 for t in trades if t["pnl"] == 0),
        "estimated_trades_S_C": sum(1 for t in trades if t["est_rows"] > 0),
        "estimated_rows_in_S_C_trades": sum(t["est_rows"] for t in trades),
        "open_at_R_trades_S_C": sum(1 for t in trades if t["closed"] is None),
        "lane_full": ("bridge log not provided" if bridge is None else bridge["lane_full_in_S_C"]),
    }
    return {
        "header": HEADER, "result": RESULT, "tests_label": LABEL, "run_now_utc": iso(now_ms),
        "constants": {"S": iso(S_MS), "event": iso(EVENT_MS), "C": iso(C_MS), "R": iso(R_MS)},
        "ledger": {"torn_last_lines_ignored": torn, "rows_at_or_after_R_ignored": cut},
        "trades": rows_out,
        "pre_S": {"count": len(pre), "sends_utc": [iso(t["ts_ms"]) for t in sorted(pre, key=lambda t: t["ts_ms"])], "days": sorted({t["day"] for t in pre}),
                  "total_lamports": sum(t["pnl"] for t in pre), "total_sol": sol(sum(t["pnl"] for t in pre)), "note": "disclosure only; not trades of this read, in no test"},
        "post_event_sends_C_R": len(post),
        "day_totals": dict(sorted(day_tot.items())),
        "tests": tests,
        "names": {"lane_bots": bots, "host_start_rows": starts, "counts": counts, "bridge": bridge},
        "disclosures": disclosures,
    }


def _yn(b: bool) -> str:
    return "yes" if b else "no"


def render_tests(name: str, t: dict[str, Any]) -> list[str]:
    L = [f"### {name} ({LABEL})", ""]
    if t["n"] == 0:
        return L + ["no trades", ""]
    a, d, b, c = t["t1_n"], t["t2_days"], t["t3_bound"], t["t4_top3"]
    L.append(f"1. n >= {N_MIN}: n = {a['value']} vs {a['threshold']}; holds: {_yn(a['holds'])}")
    L.append(f"2. at least {DAYS_MIN} distinct days and strictly more than half with a day total > 0: {d['distinct_days']} days, {d['positive_days']} positive; holds: {_yn(d['holds'])}")
    L.append(f"3. lower 90% bound of mean per trade (1000 draws, seed 1, 5th percentile): {b['lower_90_bound_mean_lamports']:.6f} lamports vs 0; holds: {_yn(b['holds'])}")
    if "note" in c:
        L.append(f"4. total after removing the 3 largest trades: {c['note']}; holds: no")
    else:
        L.append(f"4. total after removing the 3 largest trades: {c['total_after_removing_top3_lamports']} lamports ({sol(c['total_after_removing_top3_lamports'])} SOL) vs 0; holds: {_yn(c['holds'])}")
    return L + [f"all four hold: {_yn(t['all_four_hold'])} (does not change the result above)", ""]


def render_md(rep: dict[str, Any]) -> str:
    k = rep["constants"]
    L = [f"# {rep['header']}", "", f"run instant (the only clock value in this report): {rep['run_now_utc']}", "",
         f"S = {k['S']}; event (0.10 SOL config install, Erratum 1g) = {k['event']}; C = {k['C']}; R = {k['R']}", "",
         "The result follows from the Erratum 1g process event, whatever the numbers below show. The four tests below are descriptive numbers on the trades in [S, C).", "",
         f"one look: this tool writes a marker file next to --out-json and refuses to run again if it exists.", "",
         f"ledger: torn last lines ignored {rep['ledger']['torn_last_lines_ignored']}; rows at or after R ignored {rep['ledger']['rows_at_or_after_R_ignored']}", "",
         f"## Trades in [S, C): {len(rep['trades'])}", "", "| order | send (UTC) | day | stake lamports | P&L lamports | P&L SOL | closing row | open at R | estimated |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for t in rep["trades"]:
        L.append(f"| {t['order']} | {t['send_utc']} | {t['trade_day']} | {t['stake_lamports']} | {t['pnl_lamports']} | {t['pnl_sol']} | {t['closing_row']} | {_yn(t['open_at_R'])} | {_yn(t['estimated'])} |")
    p = rep["pre_S"]
    L += ["", f"## Sends before S (listed apart; {p['note']})", "", f"count {p['count']}; days {', '.join(p['days']) or 'none'}; total {p['total_lamports']} lamports ({p['total_sol']} SOL)", ""]
    L += [f"- {s}" for s in p["sends_utc"]] + ["", f"Sends in [C, R) (after the event; not trades of this read): {rep['post_event_sends_C_R']}", "", "## Day totals by trade day", ""]
    L += [f"- {d}: n {v['n']}, total {v['total_lamports']} lamports ({sol(v['total_lamports'])} SOL)" for d, v in rep["day_totals"].items()] or ["no trades"]
    L += ["", "## The four section-4 tests, pooled and by stake", ""]
    for name, t in rep["tests"].items():
        L += render_tests(name, t)
    nm = rep["names"]
    L += ["## Names and counts (EXP-029 s6)", "", "Lane bots that sent in [S, R):", ""]
    L += [f"- {b['bot_id']}: send_intents {b['send_intents_S_R']}, not_sent {b['not_sent_S_R']}, trades in [S, C) {b['trades_S_C']}, total {b['total_lamports_S_C']} lamports, sends in [C, R) {b['sends_C_R']}; result: {b['result']}" for b in nm["lane_bots"]]
    L += ["", "Decision host start rows (frozen sha, host file sha256, row instant):", ""]
    L += [f"- {s['instant_utc']}: frozen_sha {s['frozen_sha']}; host_sha256 {s['host_sha256']}" for s in nm["host_start_rows"]]
    L += ["", "Counts:", ""] + [f"- {a}: {v}" for a, v in nm["counts"].items()]
    br = nm["bridge"]
    if br is not None:
        L += ["", f"lane_full groups (Erratum 1a item 4, in [S, C)): {json.dumps(br['lane_full_groups'], sort_keys=True)}", f"bridge log torn lines ignored: {br['torn_lines_ignored']}", "", "Refusal labels in [S, R) (counts):", ""]
        L += [f"- {a}: {v}" for a, v in br["entry_refused_by_label_S_R"].items()] or ["- none"]
        L += ["", "Latch rows:", ""] + ([f"- {x['instant_utc']}: {x['latch']}" for x in br["latch_rows"]] or ["- none"])
    L += ["", "## Required disclosures (verbatim from the manager's file)", "", rep["disclosures"], ""]
    return "\n".join(L)


# ---------------------------------------------------------------- main

def _inside(p: Path, root: Path) -> bool:
    p, root = p.resolve(), root.resolve()
    return p == root or root in p.parents


def run(a: argparse.Namespace, env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    if a.now_ms is not None and not env.get(TEST_NOW_ENV):
        raise ReadError("--now-ms is for tests only")
    now_ms = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    if now_ms < R_MS:
        raise ReadError(f"refusing to run before R = {iso(R_MS)}")
    out_json, out_md = Path(a.out_json), Path(a.out_md)
    marker = Path(str(out_json) + ".done")
    if marker.exists():
        raise ReadError("one look: the marker file exists, this read already ran")
    roots = [Path(a.lane_ledger_root), Path(a.host_decisions_root)] + ([Path(a.bridge_log).parent] if a.bridge_log else [])
    for o in (out_json, out_md, marker):
        for r in roots:
            if _inside(o, r):
                raise ReadError("an output path resolves inside an input root")
    if out_json.resolve() == out_md.resolve():
        raise ReadError("--out-json and --out-md are the same path")
    disclosures = read_bytes(Path(a.disclosures)).decode("utf-8")
    rows, torn = load_ledger(Path(a.lane_ledger_root))
    starts = read_host_starts(Path(a.host_decisions_root))
    bridge = read_bridge(Path(a.bridge_log), rows) if a.bridge_log else None
    rep = assemble(rows, torn, starts, bridge, disclosures, now_ms)
    jb = (json.dumps(rep, sort_keys=True, indent=1) + "\n").encode()
    mb = render_md(rep).encode()
    for path, data in ((out_json, jb), (out_md, mb)):
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)  # last: a failed run leaves no marker
    try:
        os.write(fd, (hashlib.sha256(jb).hexdigest() + "  " + out_json.name + "\n" + hashlib.sha256(mb).hexdigest() + "  " + out_md.name + "\n").encode())
    finally:
        os.close(fd)
    print(f"exp029_read: {HEADER}; trades in [S, C) {len(rep['trades'])}; marker {marker}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lane-ledger-root", required=True)
    ap.add_argument("--host-decisions-root", required=True)
    ap.add_argument("--bridge-log")
    ap.add_argument("--disclosures", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--now-ms", type=int, help="tests only: refused unless the test env var is set")
    try:
        return run(ap.parse_args(argv))
    except ReadError as exc:
        print(f"exp029_read REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
