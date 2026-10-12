#!/usr/bin/env python3
"""H5 shadow, report-only tipauth field: the pre-declared TRIGGER PRINT ON THE TIP check (acceptance item 8).

Spec: quant-proof comment 6121547427 on PR #613, ruling 3 "Trigger print (item 8)" (the same-named tool, same window flags and imports as
tools/h5_tipauth_item4.py); item 8 of PR #610 comments 6120633640 and 6121123397; the watcher's `trigger_print_not_on_tip` (PR #614 comment
6121641942, answer 2(c)). It is a COUNT: for each unsealed pv trigger record of the run, is the trigger print's own row on the tip tape, and are its
pre-trade base and quote exactly equal to the record's own values. It makes no book claim and is NOT a proposal verdict.

  python3 tools/h5_tipauth_trigger_print.py --shadow-dir DIR --tip-dir DIR --since-ms T0 --until-ms T1 --expect-code-sha SHA40 --out FILE [--table]

WHAT IT READS (nothing else):
  * Shadow hourly files h5-shadow-YYYY-MM-DDTHH.jsonl: the decision-time keys in ALLOWED_KEYS (item 4's allow-list plus the trigger print's own
    signature, event_index, real_quote_pre, v_print, base_pre and the tip high-water keys tip_hw_slot / hw_slot). No outcome, fill, exit, ladder,
    P&L or any later-print key. Sealed records (any flag but absent / null / false) are skipped and counted before any other key is read.
  * The tip tape, ONLY lines that hold a trigger print's signature. THE TIP TAPE LIVES UNDER A PATH CONTAINING "sealed" ON THE LIVE HOST
    (/var/lib/mal/sealed/fast-trades-tip). The tool never lists, counts, sums or scans the tape for anything but the byte string
    `"signature":"<a trigger print's signature>"` (the follower writes compact JSON, tools/fast_tip_follower.py:259), in the hour file of the
    trigger's t_ms and the next two (trades-<YYYY-MM-DDTHH>.jsonl, fast_tip_follower.py:239-240, hour_of_ms :192-193). Only a line that holds that byte
    string is parsed, and only pool, signature, event_index, slot, quote_reserve, base_reserve and virtual_quote_reserves are read from it. It never
    reads a tip row whose signature is not a trigger print's, a later print, the landing-slot state, a fill, an exit or the ladder.
    The tool opens a tip file for reading only. It does not write to, rename or delete anything in either directory. No network.
  * Only PLAIN trades-<hour>.jsonl files exist: the follower appends them uncompressed (fast_tip_follower.py:256-260, mode "ab", no codec in the file).
    A compressed sibling (.gz / .zst / .xz / .bz2) of a wanted hour whose plain file is missing is NOT read; the triggers that need it are
    "undetermined" (reason tip_file_compressed_unsupported), never "absent".

Classes (per quant-proof ruling 3): ws pass (base_breaks_unresolved_settled == 0), would-pass (item 4's `would_pass`, imported; C1 to C5, C7),
refused (every other unsealed pv trigger). Outcomes per trigger: present_equal, present_different, absent, undetermined (the tip's high-water slot
minus 4 is below the trigger slot, or the trigger record lacks a usable field, or a wanted hour file is compressed). The tip high-water is the
largest tip high-water the SHADOW itself recorded in the run (trigger base_union.tip_hw_slot_global, pool-close base_union.tip_hw_slot,
hb tip_union.hw_slot): a lower bound of the tape's, so a trigger is never decided earlier than the tape allows, and the tape is never read for it.

Thresholds: would-pass class ALL present and exactly equal (null with no would-pass trigger, null while any is undetermined, false on any absent or
different). ws pass and refused are printed; an absent or different ws-pass print is an existing live exposure for the owner whatever happens to
phase 2 (`ws_pass_owner_flag`). `trigger_print_rules_clear` (NOT a proposal verdict) is true only if every threshold is true and the read is not
interim; it is never true at an interim read (until - since < 86_400_000).

Exit code: 0 the tool ran; 2 no start record; 3 missing directory; 4 refused instant; 5 --out exists. It is not a verdict.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import mmap
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

try:
    from tools import h5_tipauth_item4 as I4
except ImportError:  # run as a script: tools/ is sys.path[0]
    import h5_tipauth_item4 as I4  # type: ignore

TOOL = "h5_tipauth_trigger_print"
SETTLE_MS = I4.SETTLE_MS
DAY_MS = I4.DAY_MS
HOUR_MS = I4.HOUR_MS
TIP_MARGIN_SLOTS = 4  # the tip must be at least this many slots past the trigger slot before the trigger is decided (watcher: high-water minus 4)
TIP_HOURS_AHEAD = 2  # the trigger's hour and the next two
SPEC = {"ruling3": "PR #613 comment 6121547427", "item8": "PR #610 comments 6120633640, 6121123397", "watcher": "PR #614 comment 6121641942 answer 2(c)"}

# trigger-record keys named by the spec, all built in tools/h5_shadow.py at the trigger record builder (the `"gap": bool(p.gaps)` line is 1958)
TRIGGER_KEYS = {"signature": "tools/h5_shadow.py:1941", "event_index": "tools/h5_shadow.py:1941", "real_quote_pre": "tools/h5_shadow.py:1947",
                "v_print": "tools/h5_shadow.py:1947", "base_pre": "tools/h5_shadow.py:1947"}
# tip-row keys (observe/trade_decode.py seal_trade; rows are appended by tools/fast_tip_follower.py:256-260)
TIP_KEYS = {"pool": "observe/trade_decode.py:538", "slot": "observe/trade_decode.py:539", "signature": "observe/trade_decode.py:540",
            "event_index": "observe/trade_decode.py:541", "quote_reserve": "observe/trade_decode.py:530 (PRE-trade for PumpSwap, h5_shadow.py:101-103)",
            "base_reserve": "observe/trade_decode.py:531 (PRE-trade for PumpSwap, h5_shadow.py:101-103)",
            "virtual_quote_reserves": "observe/trade_decode.py:52-53 EVENT_V_KEYS, stamped by fast_tip_follower.py:620-675 (only with the event-V flag)"}
# the trigger side of each comparison. v_print is compared to the tip's PLURAL virtual_quote_reserves (h5_shadow.py:1351), informational only.
FIELD_NAME_MISMATCH: list = []  # every name the spec gives exists under that name (verified; see PR body); a comparison with a missing key fails closed

ALLOWED_KEYS = I4.ALLOWED_KEYS | frozenset({"signature", "event_index", "real_quote_pre", "v_print", "base_pre", "tip_hw_slot", "hw_slot"})
TIP_ROW_KEYS = frozenset(TIP_KEYS)
CLASSES = ("ws_pass", "would_pass", "refused")
OUTCOMES = ("present_equal", "present_different", "absent", "undetermined")
_COMPRESSED = (".gz", ".zst", ".xz", ".bz2")

AMBIGUITIES = [
    ("T01", "ws pass: gap", "Brief and ruling 3: 'ws pass (ws == 0)', ruling 3 adds 'gap false'. Chosen: ws == 0 whatever gap says (stricter: more triggers are checked); "
     "`ws_pass_gap_not_false` counts those whose gap is not False."),
    ("T02", "Equality", "Watcher answer 2(c): 'its pre-trade base or quote differs from the record's base_pre / real_quote_pre'. Chosen: exact integer equality of "
     "base_reserve == base_pre AND quote_reserve == real_quote_pre (bool and non-int values are unequal). v_print vs virtual_quote_reserves is reported "
     "(v_equal / v_different / v_tip_key_absent among present rows) and is NOT in the threshold: the record falls back to the pool's first V when the print "
     "had none, and the tip's V key exists only with the event-V flag."),
    ("T03", "Several tip rows with the same (pool, signature, event_index)", "Chosen: present; equal only if EVERY such row is equal (stricter)."),
    ("T04", "Hour files", "Ruling: the trigger's hour and the next two (hour of the record's t_ms, UTC). Previous hour is not read. A wanted hour whose file does not exist "
     "contributes no rows (absent if the tip is far enough)."),
    ("T05", "Tip high-water", "'the tip's high-water slot minus 4 is at or above the trigger slot'. Chosen: the tip high-water is the largest the shadow recorded in the run "
     "(trigger tip_hw_slot_global, pool-close tip_hw_slot, hb tip_union.hw_slot), because the tool may not scan the tape. A lower bound; the cost is more 'undetermined'."),
    ("T06", "A line that holds the signature bytes", "Only a line holding the exact bytes `\"signature\":\"SIG\"` is parsed; a row written with spaces after the colon would be "
     "missed (the follower writes compact JSON). A partial last line (no newline) is not parsed and is counted."),
    ("T07", "Interim", "until - since < 86_400_000 is interim: the result is never clear (null if nothing failed)."),
    ("T08", "Unusable trigger fields", "A trigger whose signature is not a string, or event_index / real_quote_pre / base_pre / slot is not an integer, is 'undetermined' "
     "(reason bad_trigger_fields), never present or absent."),
]


def _hour_name(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ms // 1000))


def wanted_hours(t_ms: int) -> list[str]:
    return [_hour_name(t_ms + k * HOUR_MS) for k in range(TIP_HOURS_AHEAD + 1)]


def classify(rec: dict) -> str:
    ws = rec.get(I4.WS_FIELD)
    if I4.isint(ws) and ws == 0:
        return "ws_pass"
    return "would_pass" if I4.would_pass(rec)[0] else "refused"


def _scan_file(path: Path, sigs: list[str], counts: collections.Counter, object_hook: Callable | None) -> dict[str, list[dict]]:
    """Rows of one tip hour file whose line holds the signature bytes of a wanted trigger print. Only those lines are parsed."""
    out: dict[str, list[dict]] = {}
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, 2)
            if size == 0:
                return out
            mm = mmap.mmap(fh.fileno(), size, access=mmap.ACCESS_READ)
    except (OSError, ValueError):
        counts["tip_file_unreadable"] += 1
        return out
    try:
        for sig in sigs:
            pat = b'"signature":"' + sig.encode("ascii", "replace") + b'"'
            pos = mm.find(pat)
            while pos != -1:
                s = mm.rfind(b"\n", 0, pos) + 1
                e = mm.find(b"\n", pos)
                if e == -1:
                    counts["tip_partial_last_line"] += 1
                else:
                    try:
                        row = json.loads(mm[s:e].decode("utf-8"), object_hook=object_hook, parse_constant=I4._bad_const)
                    except (ValueError, RecursionError):
                        counts["tip_bad_line"] += 1
                        row = None
                    if isinstance(row, dict):
                        out.setdefault(sig, []).append({k: row.get(k) for k in TIP_ROW_KEYS})
                pos = mm.find(pat, pos + len(pat))
    finally:
        mm.close()
    return out


def _outcome(t: dict, rows: list[dict]) -> tuple[str, str | None]:
    """present_equal / present_different / absent for a decidable trigger and its candidate tip rows."""
    match = [r for r in rows if r["signature"] == t["signature"] and r["pool"] == t["pool"] and I4.isint(r["event_index"]) and r["event_index"] == t["event_index"]]
    if not match:
        return "absent", None
    def eq(a: Any, b: Any) -> bool:
        return I4.isint(a) and I4.isint(b) and a == b
    all_eq = all(eq(r["base_reserve"], t["base_pre"]) and eq(r["quote_reserve"], t["real_quote_pre"]) for r in match)
    vs = ["equal" if eq(r["virtual_quote_reserves"], t["v_print"]) else ("tip_key_absent" if r["virtual_quote_reserves"] is None else "different") for r in match]
    v = "v_different" if "different" in vs else ("v_tip_key_absent" if "tip_key_absent" in vs else "v_equal")
    return ("present_equal" if all_eq else "present_different"), v


def analyze(shadow_dir: Path, tip_dir: Path, since_ms: int, until_ms: int, expect_code_sha: str, object_hook: Callable | None = None) -> dict:
    counts: collections.Counter = collections.Counter()
    snap = I4.snapshot(I4.hour_files(shadow_dir, since_ms, until_ms))
    starts = []
    for rec in I4.iter_records(snap, collections.Counter(), object_hook):
        if rec.get("type") == "start":
            t = rec.get("t_ms")
            if I4.isint(t) and since_ms <= t <= until_ms:
                starts.append({"t_ms": t, "code_sha": rec.get("code_sha")})
    starts.sort(key=lambda s: (s["t_ms"], str(s["code_sha"])))
    interim = until_ms - since_ms < DAY_MS
    res: dict = {"tool": TOOL, "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "item4_tool_sha256": hashlib.sha256(Path(I4.__file__).read_bytes()).hexdigest(),
                 "spec": SPEC, "since_ms": since_ms, "until_ms": until_ms, "interim": interim, "interim_label": "interim, not the deciding read" if interim else None,
                 "expect_code_sha": expect_code_sha, "field_name_mismatch": FIELD_NAME_MISMATCH, "tip_keys": TIP_KEYS, "trigger_keys": TRIGGER_KEYS,
                 "ambiguities": [{"id": a, "topic": b, "choice": c} for a, b, c in AMBIGUITIES], "files": [f.name for f, _ in snap]}
    res["starts"] = {"n_starts": len(starts), "origin_t_ms": starts[-1]["t_ms"] if starts else None, "origin_code_sha": starts[-1]["code_sha"] if starts else None}
    if not starts:
        res["error"] = "no start record at or after --since-ms"
        res["trigger_print_rules_clear"] = False
        res["blocking_reasons"] = ["no_start_record"]
        return res
    origin = starts[-1]["t_ms"]

    trig: list[dict] = []
    tip_hw = None
    for rec in I4.iter_records(snap, counts, object_hook):
        t, typ = rec.get("t_ms"), rec.get("type")
        if not I4.isint(t) or t < origin or t > until_ms or typ not in ("trigger", "pool", "hb"):
            continue
        if typ == "hb":
            tu = rec.get("tip_union")
            hw = tu.get("hw_slot") if isinstance(tu, dict) else None
        else:
            sl, h5 = I4.flag(rec.get("sealed")), I4.flag(rec.get("h5_look2_sealed"))
            if sl or h5:
                counts[f"skipped_{typ}_sealed"] += 1
                continue
            bu = rec.get("base_union")
            hw = bu.get("tip_hw_slot_global" if typ == "trigger" else "tip_hw_slot") if isinstance(bu, dict) else None
        if I4.isint(hw) and (tip_hw is None or hw > tip_hw):
            tip_hw = hw
        if typ != "trigger" or rec.get("variant") != "pv":
            continue
        if not isinstance(rec.get("pool"), str):
            counts["record_pool_not_str"] += 1
            continue
        cls = classify(rec)
        gap_flag = rec.get("gap") is not False
        trig.append({"cls": cls, "pool": rec.get("pool"), "t_ms": t, "slot": rec.get("slot"), "signature": rec.get("signature"),
                     "event_index": rec.get("event_index"), "real_quote_pre": rec.get("real_quote_pre"), "v_print": rec.get("v_print"),
                     "base_pre": rec.get("base_pre"), "gap_not_false": gap_flag})
    res["tip_high_water_slot_seen_by_shadow"] = tip_hw
    res["skipped"] = {"sealed_trigger": counts["skipped_trigger_sealed"], "sealed_pool": counts["skipped_pool_sealed"], "bad_line": counts["bad_line"],
                      "record_pool_not_str": counts["record_pool_not_str"]}

    # decide which triggers the tip can be expected to hold, then look each up
    dec: list[dict] = []
    for t in trig:
        ok_fields = isinstance(t["signature"], str) and bool(t["signature"]) and all(I4.isint(t[k]) for k in ("event_index", "real_quote_pre", "base_pre", "slot"))
        t["reason"] = None
        if not ok_fields:
            t["outcome"], t["reason"] = "undetermined", "bad_trigger_fields"
        elif tip_hw is None or tip_hw - TIP_MARGIN_SLOTS < t["slot"]:
            t["outcome"], t["reason"] = "undetermined", "tip_not_far_enough"
        else:
            dec.append(t)
    by_file: dict[str, set] = collections.defaultdict(set)
    compressed_needed: set = set()
    for t in dec:
        missing_compressed = False
        for h in wanted_hours(t["t_ms"]):
            p = tip_dir / f"trades-{h}.jsonl"
            if p.is_file():
                by_file[h].add(t["signature"])
            elif any((tip_dir / f"trades-{h}.jsonl{c}").exists() for c in _COMPRESSED):
                missing_compressed = True
        t["_compressed"] = missing_compressed
        if missing_compressed:
            compressed_needed.add(t["signature"])
    found: dict[tuple, list[dict]] = collections.defaultdict(list)  # (hour, sig) -> rows
    for h in sorted(by_file):
        for sig, rows in _scan_file(tip_dir / f"trades-{h}.jsonl", sorted(by_file[h]), counts, object_hook).items():
            found[(h, sig)].extend(rows)
    for t in dec:
        if t.pop("_compressed"):
            t["outcome"], t["reason"] = "undetermined", "tip_file_compressed_unsupported"
            continue
        rows = [r for h in wanted_hours(t["t_ms"]) for r in found.get((h, t["signature"]), [])]
        t["outcome"], t["v"] = _outcome(t, rows)
    res["tip_scan"] = {"tip_file_unreadable": counts["tip_file_unreadable"], "tip_bad_line": counts["tip_bad_line"], "tip_partial_last_line": counts["tip_partial_last_line"],
                       "signatures_searched": len({t["signature"] for t in dec}), "tip_files_searched": sorted(by_file)}

    classes: dict = {}
    for c in CLASSES:
        ts = [t for t in trig if t["cls"] == c]
        o = collections.Counter(t["outcome"] for t in ts)
        d = {"n": len(ts), **{k: o[k] for k in OUTCOMES},
             "undetermined_reasons": dict(sorted(collections.Counter(t["reason"] for t in ts if t["outcome"] == "undetermined").items())),
             "v_among_present": dict(sorted(collections.Counter(t.get("v") for t in ts if t["outcome"].startswith("present")).items(), key=lambda kv: str(kv[0])))}
        if c == "ws_pass":
            d["ws_pass_gap_not_false"] = sum(1 for t in ts if t["gap_not_false"])
        classes[c] = d
    res["classes"] = classes

    def all_equal(d: dict) -> bool | None:
        if d["absent"] or d["present_different"]:
            return False
        if d["n"] == 0 or d["undetermined"]:
            return None
        return True

    wp, wsd = classes["would_pass"], classes["ws_pass"]
    th = {"origin_code_sha_matches": {"met": res["starts"]["origin_code_sha"] == expect_code_sha},
          "single_start_after_t0": {"met": len(starts) == 1, "n_starts": len(starts)},
          "would_pass_n_ge_1": {"met": True if wp["n"] >= 1 else None, "n": wp["n"]},
          "would_pass_all_present_and_equal": {"met": all_equal(wp), "n": wp["n"], "present_equal": wp["present_equal"], "present_different": wp["present_different"],
                                               "absent": wp["absent"], "undetermined": wp["undetermined"]}}
    res["thresholds"] = th
    res["ws_pass_owner_flag"] = {"absent": wsd["absent"], "present_different": wsd["present_different"], "undetermined": wsd["undetermined"],
                                 "flag": bool(wsd["absent"] or wsd["present_different"]),
                                 "note": "an absent or different ws-pass trigger print is an existing live exposure: to the owner whatever happens to phase 2"}
    mets = [v["met"] for v in th.values()]
    blocking = [k for k, v in th.items() if v["met"] is not True]
    if interim:
        blocking.append("interim_not_deciding_read")
    clear = False if any(m is False for m in mets) else (None if (any(m is None for m in mets) or interim) else True)
    res["blocking_reasons"] = blocking
    res["trigger_print_rules_clear"] = clear
    res["table_note"] = "(NOT a proposal verdict)"
    return res


def table(r: dict) -> str:
    if "error" in r:
        return r["error"]
    rows = [f"{'class':11s} {'n':>5s} {'equal':>6s} {'differ':>6s} {'absent':>6s} {'undet':>6s}"]
    for c in CLASSES:
        d = r["classes"][c]
        rows.append(f"{c:11s} {d['n']:5d} {d['present_equal']:6d} {d['present_different']:6d} {d['absent']:6d} {d['undetermined']:6d}")
    for k, t in sorted(r["thresholds"].items()):
        rows.append(f"{k:36s} {str(t['met']):6s}")
    rows.append(f"trigger_print_rules_clear={r['trigger_print_rules_clear']} (NOT a proposal verdict) interim={r['interim']} blocking={r['blocking_reasons']} "
                f"ws_pass_owner_flag={r['ws_pass_owner_flag']['flag']}")
    return "\n".join(rows)


def error_result(msg: str, code: str) -> dict:
    return {"tool": TOOL, "error": msg, "trigger_print_rules_clear": False, "blocking_reasons": [code]}


def main(argv: list[str] | None = None, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shadow-dir")
    ap.add_argument("--tip-dir")
    ap.add_argument("--since-ms", type=int)
    ap.add_argument("--until-ms", type=int)
    ap.add_argument("--expect-code-sha")
    ap.add_argument("--out", help="written with exclusive create; an existing file is refused (exit 5)")
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args(argv)
    for name, val in (("--shadow-dir", a.shadow_dir), ("--tip-dir", a.tip_dir), ("--since-ms", a.since_ms), ("--until-ms", a.until_ms), ("--expect-code-sha", a.expect_code_sha)):
        if val is None:
            ap.error(f"{name} is required")
    if not re.fullmatch(r"[0-9a-f]{40}", a.expect_code_sha):
        ap.error("--expect-code-sha must be 40 lowercase hex characters")
    if not I4.on_schedule(a.since_ms, a.until_ms):
        sys.stdout.write(I4.dumps(error_result(f"--until-ms {a.until_ms} is not a scheduled instant for --since-ms {a.since_ms}", "until_ms_not_on_schedule")))
        return 4
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if now_ms < a.until_ms + SETTLE_MS:
        sys.stdout.write(I4.dumps(error_result(f"--until-ms {a.until_ms} is not yet {SETTLE_MS} ms in the past", "until_ms_not_reached")))
        return 4
    out_fh = None
    if a.out:
        try:
            out_fh = open(a.out, "x", encoding="utf-8")
        except FileExistsError:
            sys.stdout.write(I4.dumps(error_result(f"--out exists: {a.out}", "out_exists")))
            return 5
    sd, td = Path(a.shadow_dir), Path(a.tip_dir)
    if not sd.is_dir() or not td.is_dir():
        res, code = error_result("shadow or tip directory missing", "missing_directory"), 3
    else:
        res = analyze(sd, td, a.since_ms, a.until_ms, a.expect_code_sha)
        code = 2 if "error" in res else 0
    text = I4.dumps(res)
    sys.stdout.write(text)
    if out_fh is not None:
        with out_fh:
            out_fh.write(text)
    if a.table:
        sys.stderr.write(table(res) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
