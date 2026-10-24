#!/usr/bin/env python3
"""A3 applied/verdict alert (audit M4, Q50). Read-only on the three A3 flag files; prints ALERT lines for the MiScusi log. Stdlib only.

Reads ONLY <out>/a3-applied.jsonl, <out>/a3-verdicts.jsonl, <out>/a3-verdict-latest.json (flags and dates, no outcomes). Never opens shadow outputs, ledgers, bots.json or P&L.
Writes only its own state file. Checks, one pass per call (the sh loop in scripts/mal-fast/a3-applied-alert.sh calls it every 10 min):
  A3_APPLIED_NEW / A3_VERDICT_NEW   a new line since the last pass; ALERT unless the verdict is NONE (NONE prints an A3_OK line). Any 'ended' book in an applied line is an ALERT.
  A3_VERDICT_LATEST                 verdict-latest.json holds a verdict other than NONE that was not yet reported.
  A3_GUARD_STALE                    newest applied run_utc older than STALE_S (13 h): a warning before the 48 h dead-man (tools/a3_halt.py DEADMAN_S) ends the new-rule books.
  A3_FILE_PROBLEM                   a file is missing, unreadable or malformed (a check that cannot run is an alert).
First pass with no state file records a baseline: old lines are not re-alerted, but a non-NONE newest verdict is. The Discord watchdog (h5-arena-watch.py) is a root unit with a
webhook the job cannot read; this tool does not post anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STALE_S = 13 * 3600
DEADMAN_S = 48 * 3600
REPEAT_S = 3600
FILES = ("a3-applied.jsonl", "a3-verdicts.jsonl", "a3-verdict-latest.json")


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _lines(path: Path):
    """(list of dicts, problem or None). Every non-empty line must be a JSON object."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [], f"{path.name} unreadable ({type(e).__name__})"
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            return out, f"{path.name} line {i} is not JSON"
        if not isinstance(d, dict):
            return out, f"{path.name} line {i} is not an object"
        out.append(d)
    return out, None


def _state(path: Path):
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else None
    except (OSError, ValueError):
        return None


def _desc(d: dict) -> str:
    return f"verdict={d.get('verdict')} run_utc={d.get('run_utc')}"


def check(out: Path, state_path: Path, now_ms: int, stale_s: int = STALE_S) -> list[str]:
    msgs: list[str] = []
    st = _state(state_path)
    base = st is None
    st = st or {}
    applied, p1 = _lines(out / FILES[0])
    verdicts, p2 = _lines(out / FILES[1])
    for p in (p1, p2):
        if p:
            msgs.append(f"ALERT A3_FILE_PROBLEM {p}")
    latest = None
    lp = out / FILES[2]
    if lp.exists():  # the guard removes it at the start of a run: absence between runs is not a fault
        ls, p3 = _lines(lp)
        if p3:
            msgs.append(f"ALERT A3_FILE_PROBLEM {p3}")
        elif ls:
            latest = ls[-1]
    if not (out / FILES[0]).exists():
        msgs.append("ALERT A3_FILE_PROBLEM a3-applied.jsonl missing (the dead-man anchors on registration alone)")

    seen_a = int(st.get("applied_n", len(applied) if base else 0))
    seen_v = int(st.get("verdicts_n", len(verdicts) if base else 0))
    if seen_a > len(applied) or seen_v > len(verdicts):
        msgs.append("ALERT A3_FILE_PROBLEM log shorter than last pass (truncated or replaced)")
        seen_a, seen_v = min(seen_a, len(applied)), min(seen_v, len(verdicts))
    new_a = applied[seen_a:]
    new_v = verdicts[seen_v:]
    if base:
        # baseline: alert only on a non-NONE newest line
        new_a = [d for d in applied[-1:] if d.get("verdict") != "NONE"]
        new_v = [d for d in verdicts[-1:] if d.get("verdict") != "NONE"]
        msgs.append(f"A3_BASELINE applied={len(applied)} verdicts={len(verdicts)}")
    for tag, rows in (("A3_APPLIED_NEW", new_a), ("A3_VERDICT_NEW", new_v)):
        for d in rows:
            ended = d.get("ended") or {}
            n_ended = sum(len(v) for v in ended.values() if isinstance(v, (dict, list)))
            if d.get("verdict") != "NONE" or n_ended:
                msgs.append(f"ALERT {tag} {_desc(d)} ended_books={n_ended}")
            else:
                msgs.append(f"A3_OK {tag} {_desc(d)}")
    if latest and latest.get("verdict") not in (None, "NONE"):
        key = f"{latest.get('run_utc')}|{latest.get('verdict')}"
        if key != st.get("latest_key"):
            msgs.append(f"ALERT A3_VERDICT_LATEST {_desc(latest)}")
        st["latest_key"] = key

    last_ms = None
    for d in applied:
        try:
            ms = _ms(d["run_utc"])
        except (KeyError, ValueError, TypeError):
            msgs.append("ALERT A3_FILE_PROBLEM an applied line has no valid run_utc")
            continue
        last_ms = ms if last_ms is None else max(last_ms, ms)
    if last_ms is not None:
        age = (now_ms - last_ms) // 1000
        if age > stale_s and now_ms - int(st.get("stale_alert_ms", 0)) >= REPEAT_S * 1000:
            left = max(0, DEADMAN_S - age)
            msgs.append(f"ALERT A3_GUARD_STALE last_run_age_h={age / 3600:.1f} deadman_in_h={left / 3600:.1f} (new-rule books end at last run + 48 h)")
            st["stale_alert_ms"] = now_ms
        elif age <= stale_s:
            st.pop("stale_alert_ms", None)
    elif applied or (out / FILES[0]).exists():
        pass

    st.update(applied_n=len(applied), verdicts_n=len(verdicts), pass_ms=now_ms)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, sort_keys=True), encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError as e:
        msgs.append(f"ALERT A3_FILE_PROBLEM state not writable ({type(e).__name__})")
    return msgs


def main(argv=None) -> int:
    home = os.environ.get("HOME", ".")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=f"{home}/data/structure-monitor")
    ap.add_argument("--state", default=f"{home}/data/a3-alert/state.json")
    ap.add_argument("--stale-s", type=int, default=STALE_S)
    ap.add_argument("--now-ms", type=int, default=None)
    a = ap.parse_args(argv)
    now = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    stamp = datetime.fromtimestamp(now / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    msgs = check(Path(a.out), Path(a.state), now, a.stale_s)
    for m in msgs or ["A3_OK nothing new"]:
        print(f"{stamp} {m}", flush=True)
    return 1 if any(m.startswith("ALERT") for m in msgs) else 0


if __name__ == "__main__":
    sys.exit(main())
