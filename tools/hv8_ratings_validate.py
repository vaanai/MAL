#!/usr/bin/env python3
"""HV8 C3: rating-reply validator for the batches of run R (DESIGN section 4 "Ratings run R", section 5 bars 1 to 3). Prints COUNTS only; no rating content, no outcome.

Inputs: the dump dir (manifest.json with the batch plan) and a ratings dir holding one `batch-NN.jsonl` per batch, one line per reply:
    {"card_sha": <64 hex>, "seq": 0|1|2, "reply": "<the model text>", "stop_reason": null|"refusal"}   (seq 0 first rating, 1 retest, 2 re-run)
Batches 1..B come from the plan (first-rating batches, then retest batches); batch B+1 is the re-run batch (re-runs only for first cards whose seq 0 was unrated).
REFUSES (ValidationError, nothing written): a duplicate (card_sha, seq), a card_sha twice in one file, a card_sha not in that batch of the plan, a wrong seq for the
batch kind, an unknown batch file, a reply count above the cap (first + retest + 5% re-runs), more re-runs than the allowance.
Bar 1 (parse): valid first ratings (seq 0, or the seq 2 re-run of an unrated seq 0) >= ceil(0.95 x first-rating calls). A first card without a reply counts as unrated.
Bars 2 and 3 use tools.hv8_calib (kappa on first vs retest pairs; degenerate-item share). Retest ratings feed bar 2 only.
`write_final` writes first.jsonl, retest.jsonl and manifest.json INTO the ratings dir (refused if any exists); the manager then makes the dir read-only and pins the manifest sha256.
Nothing here says HV8 makes money.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from tools import hv8_calib as CAL
from tools import hv8_rate as H8

PARSE_MIN_PCT = 95
BATCH_RE = re.compile(r"^batch-(\d{2})\.jsonl$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ValidationError(Exception):
    pass


def load_plan(dump_dir: Path) -> dict:
    m = json.loads((Path(dump_dir) / "manifest.json").read_text())
    first = [s for b in m["batches"] if b["kind"] == "first" for s in b["shas"]]
    retest = [s for b in m["batches"] if b["kind"] == "retest" for s in b["shas"]]
    by_batch = {b["batch"]: b for b in m["batches"]}
    rerun_no = max(by_batch) + 1
    return {"manifest": m, "first": first, "retest": retest, "by_batch": by_batch, "rerun_batch": rerun_no, "caps": H8.call_caps(len(first))}


def parse_bar1(valid_first: int, n_first: int) -> dict:
    need = -(-PARSE_MIN_PCT * n_first // 100)
    return {"valid_first": valid_first, "first_calls": n_first, "need": need, "pass": valid_first >= need}


def validate(dump_dir: Path, ratings_dir: Path) -> dict:
    plan = load_plan(dump_dir)
    first_set, retest_set = set(plan["first"]), set(plan["retest"])
    seen: set[tuple[str, int]] = set()
    verdicts: dict[tuple[str, int], H8.RateVerdict] = {}
    n_replies, files = 0, {}
    for p in sorted(Path(ratings_dir).iterdir()):
        mt = BATCH_RE.match(p.name)
        if not mt:
            if p.name in ("manifest.json", "first.jsonl", "retest.jsonl"):
                raise ValidationError("ratings dir already holds final files")
            raise ValidationError(f"unknown file in the ratings dir: {p.name}")
        no = int(mt.group(1))
        batch = plan["by_batch"].get(no)
        if batch is None and no != plan["rerun_batch"]:
            raise ValidationError(f"unknown batch number {no}")
        body = p.read_bytes()
        files[p.name] = hashlib.sha256(body).hexdigest()
        in_file: set[str] = set()
        for ln in body.decode().splitlines():
            if not ln.strip():
                continue
            d = json.loads(ln)
            sha, seq = d.get("card_sha"), d.get("seq")
            if not isinstance(sha, str) or not SHA_RE.match(sha) or seq not in (0, 1, 2) or isinstance(seq, bool):
                raise ValidationError("a reply line needs a 64-hex card_sha and seq 0, 1 or 2")
            if sha in in_file:
                raise ValidationError("card_sha twice in one batch file")
            in_file.add(sha)
            if (sha, seq) in seen:
                raise ValidationError("duplicate (card_sha, seq)")
            seen.add((sha, seq))
            if batch is None:
                if seq != 2 or sha not in first_set:
                    raise ValidationError("re-run batch: only seq 2 for a first-rating card")
            else:
                if sha not in set(batch["shas"]):
                    raise ValidationError("card_sha is not in this batch of the plan")
                if seq != (0 if batch["kind"] == "first" else 1):
                    raise ValidationError("seq does not match the batch kind")
            verdicts[(sha, seq)] = H8.validate_rate(d.get("reply"), d.get("stop_reason"))
            n_replies += 1
    reruns = [k for k in verdicts if k[1] == 2]
    if len(reruns) > plan["caps"]["rerun"]:
        raise ValidationError("more re-runs than the 5% allowance")
    if n_replies > plan["caps"]["cap"]:
        raise ValidationError("more replies than the call cap")
    for sha_, _q in reruns:
        v0 = verdicts.get((sha_, 0))
        if v0 is None or v0.rated:
            raise ValidationError("re-run for a card whose first rating was not an unrated reply")
    first_ratings: dict[str, tuple] = {}
    for sha in plan["first"]:
        v0 = verdicts.get((sha, 0))
        if v0 is not None and v0.rated:
            first_ratings[sha] = v0.ratings
        elif (sha, 2) in verdicts and verdicts[(sha, 2)].rated:
            first_ratings[sha] = verdicts[(sha, 2)].ratings
    retest_ratings = {sha: verdicts[(sha, 1)].ratings for sha in plan["retest"] if (sha, 1) in verdicts and verdicts[(sha, 1)].rated}
    pairs = [(first_ratings[s], retest_ratings[s]) for s in plan["retest"] if s in first_ratings and s in retest_ratings]
    reasons: dict[str, int] = {}
    for v in verdicts.values():
        if not v.rated:
            reasons[v.reason or "?"] = reasons.get(v.reason or "?", 0) + 1
    defects: dict[str, int] = {}
    for v in verdicts.values():
        for d in v.why_defect:
            defects[d] = defects.get(d, 0) + 1
    out = {"hv8_ratings_validate": "ok", "replies": n_replies, "first_calls": len(plan["first"]), "retest_calls": len(plan["retest"]), "reruns": len(reruns),
           "missing_first": sum(1 for s in plan["first"] if (s, 0) not in verdicts), "missing_retest": sum(1 for s in plan["retest"] if (s, 1) not in verdicts),
           "unrated_reasons": dict(sorted(reasons.items())), "why_defects": dict(sorted(defects.items())), "caps": plan["caps"],
           "bar1": parse_bar1(len(first_ratings), len(plan["first"])), "retest_pairs": len(pairs), "files": files}
    if first_ratings:
        out["bar3"] = CAL.bar3(list(first_ratings.values()))
    if pairs:
        out["bar2"] = CAL.bar2([a for a, _ in pairs], [b for _, b in pairs])
    out["_first"], out["_retest"] = first_ratings, retest_ratings
    return out


def write_final(dump_dir: Path, ratings_dir: Path, res: dict) -> str:
    """first.jsonl, retest.jsonl, manifest.json into the ratings dir; returns the manifest sha256 (the value the pin carries)."""
    ratings_dir = Path(ratings_dir)
    for n in ("first.jsonl", "retest.jsonl", "manifest.json"):
        if (ratings_dir / n).exists():
            raise ValidationError("final files already exist")
    dump_sha = hashlib.sha256((Path(dump_dir) / "manifest.json").read_bytes()).hexdigest()
    jl = lambda d: "".join(json.dumps({"card_sha": k, "ratings": list(v)}, sort_keys=True, separators=(",", ":")) + "\n" for k, v in sorted(d.items())).encode()  # noqa: E731
    fb, rb = jl(res["_first"]), jl(res["_retest"])
    (ratings_dir / "first.jsonl").write_bytes(fb)
    (ratings_dir / "retest.jsonl").write_bytes(rb)
    man = {k: v for k, v in res.items() if not k.startswith("_")} | {"dump_manifest_sha256": dump_sha, "first_sha256": hashlib.sha256(fb).hexdigest(),
                                                                         "retest_sha256": hashlib.sha256(rb).hexdigest(), "n_first_valid": len(res["_first"])}
    body = (json.dumps(man, sort_keys=True, indent=1) + "\n").encode()
    (ratings_dir / "manifest.json").write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate HV8 rating replies (counts only).")
    ap.add_argument("--dump", required=True)
    ap.add_argument("--ratings", required=True)
    ap.add_argument("--write-final", action="store_true", help="write first.jsonl / retest.jsonl / manifest.json into the ratings dir and print the manifest sha256")
    a = ap.parse_args(argv)
    try:
        res = validate(Path(a.dump), Path(a.ratings))
        sha = write_final(Path(a.dump), Path(a.ratings), res) if a.write_final else None
    except ValidationError as e:
        print(json.dumps({"hv8_ratings_validate": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in res.items() if not k.startswith("_")} | ({"manifest_sha256": sha} if sha else {}), sort_keys=True, separators=(",", ":")))
    return 0 if res["bar1"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
