"""r4bt_a3: the A3 ENTRY of the arena book r4-b-T (`r4bt-a1`; DEC-031 Amendment 25 DRAFT item 12, PR #1187). PAPER ONLY. Registry write only (one stop line). No key, no RPC.

The A3 regime of r4-b-T is its OWN (item 12). It does not touch EXP-027's A3 rule (QP-RULING-1102-r4b-a3-record R2) or the H5 books' (`tools/a3_halt.py`, `tools/a3_redeploy_check.py`,
whose verdicts treat the four BOOST flags and `synthetic_share_high` as always-hard and so cannot serve a book that reads neither). This tool reads the SAME daily record
(`tools/pump_structure_monitor.py` output, one JSON object: `run_utc`, `accounts`, `programs`, `halt.flags`) and the SAME pins file (`tools/pump_structure_pins.json`), through
`tools.a3_redeploy_check.pins_diff` and `_norm_flags` (imported, not copied), and sorts the record into:

  HALT         (hard; the window ends at the run's `run_utc`):
                 - a typed pins difference of kind `account` (bonding_fee_config, pumpswap_fee_config, pumpswap_global_config) or of the `fees` PROGRAM's deploy slot
                   (Amendment 4's hard-halt list);
                 - the follower's decoder pin failing (`--follower-status`: `decoder_blobs_pinned` is not true);
                 - `pins_changed` set with no typed difference, a pins difference the flags do not show, or any halt flag outside the six known ones (fail closed).
               A hard halt inside the counted window ENDS the window at its instant. If the read boundary has not come by then the read is NOT_DECIDABLE (item 12); the pricer
               refuses a registry line stopped before the boundary.
  DECODE_FAIL  a program-only redeploy whose decode check FAILed or ERRORed (`--decode-checks`): "a decode change found later in the window means NOT_DECIDABLE" (QP-RULING-1102
               Addendum 1, A1.2 item 7). Ends the window at the redeploy's check instant like HALT. ASSUMPTION until Q2 is ruled: the check is `tools.a3_redeploy_check`'s per-write
               verdict (decoder parity on two pinned samples, IDL unchanged), supplied as `{"redeploys": [{"name", "slot", "verdict": "PASS"|"FAIL"|"ERROR", "at_ms"}]}`.
  RECORD_ONLY  a program-only (pump / pumpswap) redeploy, every write with a PASS decode check. Disclosed (slot, UTC); the window continues.
  NEEDS_CHECK  a program-only redeploy with NO decode-check result yet: the window continues, the verdict says the check is owed (the manager runs it; Q2).
  NONE         no difference and no flag.
  ERROR        a broken record (no `halt.flags`) or an unreadable input: nothing is applied.
The four BOOST flags and `synthetic_share_high` never halt r4-b-T (r4-b reads no BOOST flag and no synthetic class, EXP-027 section 3.6 (b) and (d)): they are listed under
`not_halting` so they are recorded and disclosed.

    python -m tools.r4bt_a3 classify --record REC.json --pins PINS.json [--follower-status S.json] [--decode-checks D.json] [--out V.json]
    python -m tools.r4bt_a3 apply --verdict V.json --registry bots.json --applied-log LOG.jsonl

`apply` appends ONE stop line (`arena_paper.stop_bot`) for a HALT or DECODE_FAIL verdict, once (an earlier stop is never moved: a second apply is a no-op that is logged), and one line
to the applied log for every verdict it sees. The daily job (research-0's pump_structure_monitor, then this tool) is a host-side MiScusi job the manager schedules; the dead-man rule
of `tools/a3_halt.py` is not included here (ASSUMPTION, listed in the PR).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import r4bt_gate as G
from tools.a3_redeploy_check import ALL_HALT_FLAGS, BOOST_FLAGS, CheckError, _norm_flags, pins_diff
from tools.arena_paper import ArenaRefused, load_registry, stop_bot

SCHEMA = "r4bt_a3_v1"
HARD_ACCOUNTS = ("bonding_fee_config", "pumpswap_fee_config", "pumpswap_global_config")
HARD_PROGRAMS = ("fees",)
RECORD_PROGRAMS = ("pump", "pumpswap")
NOT_HALTING = BOOST_FLAGS + ("synthetic_share_high",)
VERDICTS = ("NONE", "NEEDS_CHECK", "RECORD_ONLY", "DECODE_FAIL", "HALT", "ERROR")
ENDING = ("HALT", "DECODE_FAIL")
MIN_MS = 1_600_000_000_000


def _verdict(rec: Mapping, verdict: str, halt_at_ms: int | None, **kw: Any) -> dict:
    return {"schema": SCHEMA, "run_utc": rec.get("run_utc"), "verdict": verdict, "halt_at_ms": halt_at_ms, **kw}


def classify(rec: Mapping[str, Any], pins: Mapping[str, Any], *, follower_decoder_pinned: bool | None = None, decode_checks: Mapping[str, Any] | None = None) -> dict:
    """Pure. `follower_decoder_pinned` None = not supplied (not a halt, but the verdict says so); False = a decoder-pin failure (HALT)."""
    try:
        run_ms = G.utc_ms(rec.get("run_utc"))
        flags = _norm_flags(rec)
    except (G.Refused, CheckError) as exc:
        return _verdict(rec, "ERROR", None, reasons=[f"broken record: {exc}"], hard=[], record_only=[], not_halting=[], needs_check=[])
    diffs = pins_diff(rec, pins)
    hard: list[str] = []
    record_only: list[dict] = []
    for d in diffs:
        if d["kind"] == "account" and d["name"] in HARD_ACCOUNTS:
            hard.append(f"account pin {d['name']} changed")
        elif d["kind"] == "program" and d["name"] in HARD_PROGRAMS:
            hard.append(f"fees program deploy slot {d['old']} -> {d['new']}")
        elif d["kind"] == "program" and d["name"] in RECORD_PROGRAMS:
            record_only.append({"name": d["name"], "slot": d["new"], "old_slot": d["old"], "deploy_utc": d.get("deploy_utc")})
        else:
            hard.append(f"unknown pins difference {d['kind']} {d['name']}")
    pc = flags["pins_changed"]
    if pc["halt"] and not diffs:
        hard.append("pins_changed set but no typed difference found")
    if diffs and not pc["halt"]:
        hard.append("pins differ from the record but the pins_changed flag is not set (degraded record)")
    if not pc["evaluated"]:
        hard.append("pins_changed unevaluated")
    for k, v in flags.items():
        if k not in ALL_HALT_FLAGS and v["halt"]:
            hard.append(f"unknown halt flag {k}")
    not_halting = sorted(k for k in NOT_HALTING if flags.get(k, {}).get("halt"))
    if follower_decoder_pinned is False:
        hard.append("follower decoder pin failing")
    extra = {"hard": hard, "record_only": record_only, "not_halting": not_halting,
             "follower_decoder_pinned": follower_decoder_pinned, "program_redeploys": [dict(r) for r in record_only]}
    if hard:
        return _verdict(rec, "HALT", run_ms, reasons=hard, needs_check=[], **{k: v for k, v in extra.items() if k != "hard"}, hard=hard)
    needs: list[dict] = []
    fails: list[tuple[int, dict]] = []
    results = {(r.get("name"), r.get("slot")): r for r in ((decode_checks or {}).get("redeploys") or [])}
    for w in record_only:
        res = results.get((w["name"], w["slot"]))
        if res is None:
            needs.append(w)
        elif res.get("verdict") != "PASS":
            at = res.get("at_ms")
            fails.append((int(at) if isinstance(at, int) and not isinstance(at, bool) and at >= MIN_MS else run_ms, dict(w, check=res.get("verdict"))))
    if fails:
        at, w = min(fails, key=lambda x: x[0])
        return _verdict(rec, "DECODE_FAIL", at, reasons=[f"decode check {w['check']} for {w['name']} slot {w['slot']}"], needs_check=needs, hard=[], **{k: v for k, v in extra.items() if k != "hard"})
    if needs:
        return _verdict(rec, "NEEDS_CHECK", None, reasons=["program redeploy without a decode-check result (owed; Q2)"], needs_check=needs, hard=[], **{k: v for k, v in extra.items() if k != "hard"})
    if record_only:
        return _verdict(rec, "RECORD_ONLY", None, reasons=["program-only redeploy, decode check PASS: disclosed"], needs_check=[], hard=[], **{k: v for k, v in extra.items() if k != "hard"})
    return _verdict(rec, "NONE", None, reasons=[], needs_check=[], hard=[], **{k: v for k, v in extra.items() if k != "hard"})


def append_log(path: str | Path, entry: Mapping[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def validate(v: Mapping[str, Any], now_ms: int) -> None:
    if v.get("schema") != SCHEMA or v.get("verdict") not in VERDICTS:
        raise G.Refused("not an r4bt_a3 verdict")
    h = v.get("halt_at_ms")
    if v["verdict"] in ENDING:
        if not isinstance(h, int) or isinstance(h, bool) or not MIN_MS <= h <= now_ms + 300_000:
            raise G.Refused(f"{v['verdict']} needs an epoch-ms halt_at_ms in range")
    elif h is not None:
        raise G.Refused(f"{v['verdict']} carries a halt_at_ms")


def apply(v: Mapping[str, Any], registry: str | Path, applied_log: str | Path, *, now_ms: int | None = None, bot: str = G.BOT_ID) -> dict:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    G.check_path(registry, "the registry")
    validate(v, now)
    if v["verdict"] == "ERROR":
        raise G.Refused("an ERROR verdict applies nothing and is not logged as applied (the job is loud; no dead-man reset)")
    e = load_registry(registry).get(bot)
    if e is None:
        raise G.Refused(f"{bot} is not registered")
    action = "none"
    if v["verdict"] in ENDING:
        if e["stopped_at_ms"] is not None:
            action = "already_stopped"  # an earlier stop is never moved
        else:
            try:
                stop_bot(bot, int(v["halt_at_ms"]), registry)
                action = "stopped"
            except ArenaRefused as exc:
                raise G.Refused(str(exc)) from None
    out = {"applied_ms": now, "bot_id": bot, "run_utc": v.get("run_utc"), "verdict": v["verdict"], "halt_at_ms": v.get("halt_at_ms"), "action": action,
           "record_only": v.get("record_only") or [], "not_halting": v.get("not_halting") or [], "needs_check": v.get("needs_check") or []}
    append_log(applied_log, out)
    return out


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b-T A3 entry.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--record", required=True)
    c.add_argument("--pins", required=True)
    c.add_argument("--follower-status", default=None)
    c.add_argument("--decode-checks", default=None)
    c.add_argument("--out", default=None)
    a_ = sub.add_parser("apply")
    a_.add_argument("--verdict", required=True)
    a_.add_argument("--registry", required=True)
    a_.add_argument("--applied-log", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "classify":
            rec = json.loads(Path(a.record).read_text())
            pins = json.loads(Path(a.pins).read_text())
            pinned = None
            if a.follower_status:
                pinned = json.loads(Path(a.follower_status).read_text()).get("decoder_blobs_pinned")
                pinned = pinned if isinstance(pinned, bool) else False  # present but not a boolean: fail closed
            checks = json.loads(Path(a.decode_checks).read_text()) if a.decode_checks else None
            v = classify(rec, pins, follower_decoder_pinned=pinned, decode_checks=checks)
            text = json.dumps(v, sort_keys=True)
            if a.out:
                if Path(a.out).exists():
                    raise G.Refused(f"{a.out} exists")
                Path(a.out).write_text(text + "\n")
            print(text)
            return 11 if v["verdict"] == "ERROR" else 0
        res = apply(json.loads(Path(a.verdict).read_text()), a.registry, a.applied_log, now_ms=now_ms)
        print(json.dumps(res, sort_keys=True))
        return 0
    except (G.Refused, ValueError, OSError) as e:
        print(f"r4bt_a3: REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
