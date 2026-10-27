#!/usr/bin/env python3
"""hv7_rehearsal_validate: the HV7 rank-rehearsal bars exactly as ruled by QP (comment 6432750602 (c)) and written in DESIGN section 7 (Amendment 2026-10-27).

Inputs: the dump's prompts (prompts/batch-NN.jsonl: chunk_id, set_id, shuffle, card_ids in display order) and replies (JSON lines {chunk_id, text}). A prompt with no
reply counts as a parse failure. Validity is tools.hv7_rank.validate_rank (a full permutation of the shown ids). Prints COUNTS and shares only: no pool id, price,
outcome or rank IC exists here, and none is computed. This is a kill-only floor, not a skill test.

Sums run over the valid-permutation calls i (N_i = len(card_ids)); n = number of calls; S = sets with BOTH shuffles valid, s over those sets.
  1 parse        valid >= ceil(0.95 x calls)
  2 slot 1       share of rank-1 picks at display slot 1 <= 2 x mean_i(1/N_i)
  3 other slots  for each display slot j >= 2: E_j = sum over valid calls with N_i >= j of 1/N_i, O_j = rank-1 picks at slot j; O_j <= 2 x E_j for every slot with
                 E_j >= 9; slots with E_j < 9 are report-only. Chi-square (expected counts E_j, slots with E_j >= 5) is report-only.
  4 consistency  top-1 A/B agreement over the S sets >= max(0.20, (sum_s 1/N_s + 2.1 x sqrt(sum_s (1/N_s)(1 - 1/N_s))) / S)
  5 content      (report-only) agreement >= max(0.30, 2.4 x mean_s(1/N_s))
  6 sample       sets in the prompts < 30 -> NOT_DECIDABLE at hourly periods (re-run once with the :32 instants added; no further re-run when the manifest is
                 already a half-hour run)
INPUT INTEGRITY (refused, exit 2, before any bar): the prompts must match manifest.json (every listed file's sha256 and call count, no unlisted batch file, total
calls and sets, a 40-hex git_head); a duplicate reply key (chunk_id, set_id, shuffle) is refused (a re-asked call would pick a variant by result); replies that match no
prompt are counted and printed (`replies_matching_no_prompt`), never used. The result prints git_head and half_hour.
Result: NOT_DECIDABLE if bar 6 trips; else PASS iff bars 1, 2, 3 and 4 pass, else FAIL.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

from tools import hv7_rank as RK

MIN_SETS = 30
PARSE_SHARE = 0.95
AGREE_FLOOR = 0.20


class InputRefused(Exception):
    """Carries a fixed reason string only (never a value from the files)."""


def validate(prompts: Mapping[str, Mapping[str, Any]], replies: Mapping[str, Any], *, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    calls = len(prompts)
    top: dict[str, int] = {}                      # chunk_id -> display slot (1-based) of the rank-1 pick
    top_id: dict[str, str] = {}
    for cid, pr in prompts.items():
        v = RK.validate_rank(replies.get(cid), pr["card_ids"])
        if v.ok:
            top_id[cid] = v.order[0]
            top[cid] = list(pr["card_ids"]).index(v.order[0]) + 1
    valid = len(top)
    need = math.ceil(PARSE_SHARE * calls - 1e-9)
    parse_pass = calls > 0 and valid >= need
    n_i = {cid: len(prompts[cid]["card_ids"]) for cid in top}
    mean_inv = (sum(1.0 / n for n in n_i.values()) / valid) if valid else float("nan")
    slot1 = (sum(1 for s in top.values() if s == 1) / valid) if valid else float("nan")
    slot1_bar = 2.0 * mean_inv
    slot1_pass = valid > 0 and slot1 <= slot1_bar
    slots = {}
    other_pass = valid > 0
    chi = 0.0
    for j in range(1, RK.N_MAX + 1):
        e = math.fsum(1.0 / n for n in n_i.values() if n >= j)
        o = sum(1 for s in top.values() if s == j)
        decided = e >= 9
        ok = (o <= 2.0 * e) if decided else None
        if j >= 2 and decided and not ok:
            other_pass = False
        if e >= 5:
            chi += (o - e) ** 2 / e
        slots[str(j)] = {"expected": e, "observed": o, "bar_applies": bool(decided and j >= 2), "ok": ok if j >= 2 else None}
    by_set: dict[Any, dict[str, str]] = {}
    for cid, pr in prompts.items():
        by_set.setdefault(pr["set_id"], {})[pr["shuffle"]] = cid
    pairs = [(s["A"], s["B"]) for s in by_set.values() if "A" in s and "B" in s and s["A"] in top and s["B"] in top]
    S = len(pairs)
    inv = [1.0 / len(prompts[a]["card_ids"]) for a, _ in pairs]
    agree = (sum(1 for a, b in pairs if top_id[a] == top_id[b]) / S) if S else float("nan")
    agree_bar = max(AGREE_FLOOR, (sum(inv) + 2.1 * math.sqrt(math.fsum(p * (1 - p) for p in inv))) / S) if S else float("nan")
    agree_pass = S > 0 and agree >= agree_bar
    content_bar = max(0.30, 2.4 * (sum(inv) / S)) if S else float("nan")
    n_sets = len(by_set)
    hist: dict[str, int] = {}
    for s in by_set.values():
        any_cid = next(iter(s.values()))
        k = str(len(prompts[any_cid]["card_ids"]))
        hist[k] = hist.get(k, 0) + 1
    undecidable = n_sets < MIN_SETS
    half = bool(manifest.get("half_hour")) if manifest else None
    stray = len(set(replies) - set(prompts))
    bars = {"parse": parse_pass, "slot1": slot1_pass, "other_slots": other_pass, "consistency": agree_pass}
    result = "NOT_DECIDABLE" if undecidable else ("PASS" if all(bars.values()) else "FAIL")
    return {"result": result, "bars": bars, "calls": calls, "valid": valid, "valid_needed": need, "sets": n_sets, "sets_both_valid": S, "n_histogram": dict(sorted(hist.items())),
            "slot1_share": slot1, "slot1_bar": slot1_bar, "mean_inv_n": mean_inv, "slots": slots, "chi_square_report_only": chi,
            "agreement": agree, "agreement_bar": agree_bar, "content_driven_report_only": bool(S and agree >= content_bar), "content_bar": content_bar,
            "not_decidable_below_sets": MIN_SETS, "rerun_with_half_hour": bool(undecidable and half is not True), "git_head": (manifest or {}).get("git_head"), "half_hour": half,
            "replies_matching_no_prompt": stray, "outcome_free": True}


def load(prompts_dir: Path, replies_path: Path, manifest_path: Path | None = None) -> tuple[dict, dict, dict]:
    """Returns (prompts, replies, manifest). Refuses (InputRefused) unless the prompts equal the manifest; refuses duplicate reply keys."""
    prompts_dir = Path(prompts_dir)
    mp = Path(manifest_path) if manifest_path is not None else prompts_dir.parent / "manifest.json"
    try:
        man = json.loads(mp.read_text())
    except (OSError, ValueError):
        raise InputRefused("manifest_unreadable") from None
    head = man.get("git_head")
    if not isinstance(head, str) or len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise InputRefused("manifest_git_head_missing")
    listed = {Path(f["file"]).name: f for f in man.get("files", [])}
    on_disk = {p.name for p in prompts_dir.glob("batch-*.jsonl")}
    if on_disk != set(listed) or not listed:
        raise InputRefused("batch_files_differ_from_manifest")
    prompts: dict = {}
    total = 0
    for name in sorted(listed):
        data = (prompts_dir / name).read_bytes()
        lines = [x for x in data.splitlines() if x.strip()]
        if hashlib.sha256(data).hexdigest() != listed[name]["sha256"] or len(lines) != listed[name]["calls"]:
            raise InputRefused("batch_file_sha_or_calls_differ")
        for line in lines:
            r = json.loads(line)
            k = r["chunk_id"] + ":" + str(r["set_id"]) + ":" + r["shuffle"]
            if k in prompts:
                raise InputRefused("duplicate_prompt_key")
            prompts[k] = r
        total += len(lines)
    if total != man.get("calls") or len(prompts) != total or len({r["set_id"] for r in prompts.values()}) != man.get("sets"):
        raise InputRefused("totals_differ_from_manifest")
    replies: dict = {}
    for line in Path(replies_path).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            k = r["chunk_id"] + ":" + str(r.get("set_id")) + ":" + str(r.get("shuffle"))
            if k in replies:
                raise InputRefused("duplicate_reply_key")
            replies[k] = r.get("text")
    return prompts, replies, man


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV7 rehearsal bars (QP 6432750602 (c)). Counts only.")
    ap.add_argument("--prompts-dir", required=True)
    ap.add_argument("--manifest", default=None, help="default: <prompts-dir>/../manifest.json")
    ap.add_argument("--replies", required=True, help="JSON lines {chunk_id, set_id, shuffle, text}")
    a = ap.parse_args(argv)
    try:
        pr, rp, man = load(Path(a.prompts_dir), Path(a.replies), Path(a.manifest) if a.manifest else None)
        res = validate(pr, rp, manifest=man)
    except InputRefused as e:
        print(json.dumps({"hv7_rehearsal_validate": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only
        print(json.dumps({"hv7_rehearsal_validate": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    print(json.dumps(res, sort_keys=True, separators=(",", ":")))
    return {"PASS": 0, "FAIL": 1, "NOT_DECIDABLE": 4}[res["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
