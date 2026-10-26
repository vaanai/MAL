#!/usr/bin/env python3
"""HV6 v6.1 blind rate rehearsal: prompt builder (pre-registration ARTIFACTS/lab/hv6-v61-rehearsal-prereg.md). PAPER. No API call, no network.

Copies the pattern of /data2/mal/hv6-rate-rehearsal/build_prompts.py (P58) with three differences, all fixed before the build:
  1. the system block is the v6.1 seed (tools/hv6_playbooks_v61/GEN_v0.txt, sha256 598f7aaa...), loaded by hv6_agents.load_playbook("v0", "v6.1");
  2. the 192 cards exclude BOTH earlier samples (48 of /data2/mal/hv6-rehearsal, 192 of /data2/mal/hv6-rate-rehearsal);
  3. a new seed string, so the pick differs from P58's.
Same 1,560-card file (drill cards md5 120dbe2a...), same days 2026-10-21..23, same 16 cards per (day, setup) cell, same four-card packing (8 files x 6 calls).
Reads ONLY <drill root>/real-a/v6/cards/*.jsonl and the two prior cards-sample.jsonl files (card ids only). Joins no outcome, exit or price. Prints no card.

Usage: /data/mal/venv/bin/python -B tools/hv6_v61_rehearsal_build.py <expected 40-hex head> [--code /data2/mal/wt/hv6-v61-build] [--out /data2/mal/hv6-v61-rehearsal]
The code checkout must be clean, at the expected head, and must not exist with prompts already written (write-once)."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DRILL = Path("/data/mal/arena/haiku-v6/drill/real-5071e29a-r16g-20261026")
RUN = DRILL / "real-a" / "v6"
DAYS = ("2026-10-21", "2026-10-22", "2026-10-23")
CARDS_MD5 = "120dbe2aba1c4e66e44f5825b2df589a"
CARDS_TOTAL = 1560
V61_SEED_SHA = "598f7aaa22339f8e0e7a5e6d6b83eba1efe9be0c81f9a6721f3be33de380b738"
HEADER_SHA = "2358077afc82d9daddd896371e6299c058cf8fe504a3ef48bf82f29b56e93821"
ENTRY_TEMPLATE_SHA = "aa9224d34aa9e7eaad382050f38d52257a0c79fb1b06ef3805bfb222ab187e68"
PRIORS = {Path("/data2/mal/hv6-rehearsal/cards-sample.jsonl"): 48, Path("/data2/mal/hv6-rate-rehearsal/cards-sample.jsonl"): 192}
SETUPS = ("PULLBACK", "BREAKOUT", "SECOND_WAVE", "BOARD")
PER_CELL = 16
SEED = "hv6-v61-rehearsal-seed1"
SEQ = 0
LETTERS = "ABCDEFGH"


def git(code: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(code), *args], capture_output=True, text=True, check=True).stdout.strip()


def cards_md5(root: Path) -> str:
    h = hashlib.md5()
    for f in sorted(Path(root, "cards").rglob("*.jsonl")):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def load_rows(p4):
    rows = []
    for d in DAYS:
        for line in (RUN / "cards" / f"{d}.jsonl").read_text().splitlines():
            r = json.loads(line)
            c = r["card"]
            assert p4.card_sha256(c) == r["sha"]
            dt = datetime.fromtimestamp(c["as_of_ms"] / 1000, timezone.utc)
            assert dt.strftime("%Y-%m-%d") == d
            setup = c["setup"]["type"] if isinstance(c.get("setup"), dict) else "BOARD"
            rows.append({"sha": r["sha"], "card": c, "day": d, "setup": setup})
    return rows


def pick(rows, prior: set[str]):
    picked = []
    for d in DAYS:
        for s in SETUPS:
            st = sorted((r for r in rows if r["day"] == d and r["setup"] == s and r["sha"] not in prior), key=lambda r: (r["card"]["as_of_ms"], r["sha"]))
            n = len(st)
            if n < PER_CELL:
                sys.exit(f"refused: cell {d}|{s} has {n} fresh cards < {PER_CELL}")
            for b in range(PER_CELL):  # 16 equal-count time bins, one card per bin (lowest seeded hash)
                bin_ = st[b * n // PER_CELL:(b + 1) * n // PER_CELL]
                bin_.sort(key=lambda r: sha(SEED + "|" + r["sha"]))
                picked.append(bin_[0])
    shas = {r["sha"] for r in picked}
    assert len(picked) == 192 and len(shas) == 192 and not (shas & prior)
    return picked


def pack(picked):
    """As P58: interleave strata into 48 four-card chunks, no two cards of one pool in a chunk."""
    order = sorted(picked, key=lambda r: (SETUPS.index(r["setup"]), r["day"], r["card"]["as_of_ms"]))
    chunks = [[order[i]] for i in range(48)]
    for r in [order[i + 48 * k] for k in (1, 2, 3) for i in range(48)]:
        i0 = order.index(r) % 48
        for t in range(48):
            ch = chunks[(i0 + t) % 48]
            if len(ch) < 4 and all(x["card"]["id"] != r["card"]["id"] for x in ch):
                ch.append(r)
                break
        else:
            sys.exit("refused: cannot place card without pool clash")
    assert all(len(c) == 4 for c in chunks)
    return chunks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("expected_head")
    ap.add_argument("--code", default="/data2/mal/wt/hv6-v61-build")
    ap.add_argument("--out", default="/data2/mal/hv6-v61-rehearsal")
    a = ap.parse_args(argv)
    code, out = Path(a.code), Path(a.out)
    head, dirty = git(code, "rev-parse", "HEAD"), git(code, "status", "--porcelain", "--ignored")
    if head != a.expected_head or dirty:
        sys.exit(f"refused: checkout head {head} dirty={bool(dirty)}")
    if (out / "prompts").exists() or (out / "manifest.json").exists():
        sys.exit("refused: write-once, prompts or manifest already exist")
    sys.path.insert(0, str(code))
    from tools import hv4_prompts as p4  # noqa: E402
    from tools import hv6_agents as A  # noqa: E402

    got = cards_md5(RUN)
    if got != CARDS_MD5:
        sys.exit(f"refused: cards md5 {got}")
    rows = load_rows(p4)
    if len(rows) != CARDS_TOTAL:
        sys.exit(f"refused: {len(rows)} cards, want {CARDS_TOTAL}")
    prior: set[str] = set()
    for p, n in PRIORS.items():
        ids = {json.loads(line)["card_id"] for line in p.read_text().splitlines() if line.strip()}
        assert len(ids) == n, p
        prior |= ids
    assert len(prior) == 240
    picked = pick(rows, prior)

    pb = A.load_playbook("v0", "v6.1")
    if pb.sha256 != V61_SEED_SHA or pb.header_sha256 != HEADER_SHA:
        sys.exit("refused: v6.1 playbook pins differ")
    arm_check = Counter()
    for r in picked:
        by_arm = {x: A.build_entry_request(x, pb, [r["card"]], SEQ, max_cards=1) for x in A.llm_arms()}
        f = by_arm["F"]
        for x, q in by_arm.items():
            arm_check[f"{x}.system_equal_F"] += int(q.system == f.system)
            ul, fl = q.user_text.splitlines(), f.user_text.splitlines()
            diff = [i for i, (u, v) in enumerate(zip(ul, fl)) if u != v]
            arm_check[f"{x}.user_equal_F_except_nonce_line"] += int(len(ul) == len(fl) and all(fl[i].startswith("Request tag (opaque, ignore it): ") for i in diff))

    chunks = pack(picked)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompts").mkdir()
    files = {}
    for bi, L in enumerate(LETTERS):
        lines = []
        for j in range(6):
            cs = chunks[bi * 6 + j]
            q = A.build_entry_request("F", pb, [r["card"] for r in cs], SEQ, max_cards=4)
            assert q.template_sha256 == ENTRY_TEMPLATE_SHA and q.playbook_sha256 == V61_SEED_SHA
            lines.append(json.dumps({"chunk_id": f"v{L}{j + 1}", "card_ids": [r["sha"] for r in cs], "system": q.system[0]["text"], "user": q.user_text},
                                    ensure_ascii=False, separators=(",", ":")))
        p = out / "prompts" / f"batch-{L}.jsonl"
        p.write_text("\n".join(lines) + "\n")
        files[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    sp = out / "cards-sample.jsonl"
    sp.write_text("\n".join(json.dumps({"card_id": r["sha"], "card": r["card"]}, separators=(",", ":")) for r in picked) + "\n")
    files[sp.name] = hashlib.sha256(sp.read_bytes()).hexdigest()
    body = A.build_entry_request("F", pb, [picked[0]["card"]], SEQ, max_cards=4).body()
    manifest = {
        "stage": "hv6-v61-rehearsal prompts only, no model call", "prereg": "ARTIFACTS/lab/hv6-v61-rehearsal-prereg.md", "code_sha": head, "code_checkout": str(code),
        "cards_source": str(RUN / "cards"), "cards_md5_drill_rule": got, "cards_total": len(rows),
        "excluded_prior_samples": {str(p): n for p, n in PRIORS.items()}, "seed": SEED, "cards_per_call": 4,
        "sample": {"n": len(picked), "rule": "per (day, setup) 16 equal-count time bins over the cards in neither prior sample, 1 card per bin by lowest sha256(seed|card_sha)",
                   "by_day": dict(Counter(r["day"] for r in picked)), "by_setup": dict(Counter(r["setup"] for r in picked)),
                   "distinct_pools": len({r["card"]["id"] for r in picked}), "overlap_with_priors": 0},
        "stratum_sizes": {f"{d}|{s}": sum(1 for r in rows if r["day"] == d and r["setup"] == s) for d in DAYS for s in SETUPS},
        "system_prompt_sha256": pb.sha256, "header_sha256": pb.header_sha256, "entry_template_sha256": ENTRY_TEMPLATE_SHA,
        "request_params": {"model": body["model"], "max_tokens": body["max_tokens"], "thinking": body.get("thinking"), "temperature": body.get("temperature", "absent"),
                           "system_block": "one text block = tools/hv6_playbooks_v61/GEN_v0.txt verbatim, cache_control {type: ephemeral}",
                           "messages": "one user message = 'user' field", "seq_for_nonce": SEQ},
        "arm_equality_over_192_single_card_requests": dict(arm_check), "calls": 48, "files": {"A..H": "6 calls each"}, "files_sha256": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: manifest[k] for k in ("code_sha", "system_prompt_sha256", "arm_equality_over_192_single_card_requests", "sample", "request_params")}, indent=1))
    print("manifest_sha256", hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
