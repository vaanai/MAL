#!/usr/bin/env python3
"""P28-H: build the 50 label-blind debug cards (DESIGN C2) from Stage 0d output.

Input: stage0d/fetched.jsonl (no label column). Selection: status ok, host_class ipfs_cid, stage0c
json_ok, hash order of sha256("P28H-CARDS" + mint). Outputs under --out:
  debug_cards.json   card id + metadata text fields only (no mint, no label, no block)
  debug_map.json     card id -> mint (for TRAIN/TEST exclusion; NOT for the subagents)
Cards are ordered and numbered by a second hash so the id carries no block or order information.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

N_CARDS = 50
CAPS = {"name": 60, "symbol": 60, "description": 600}


def link_shape(url: str) -> str:
    """Host plus path shape only (never a visit): 'x.com /<1 segment>'. Empty stays empty."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return "unparseable"
    host = (parts.hostname or "").lower().removeprefix("www.")
    segs = [s for s in parts.path.split("/") if s]
    return f"{host} /<{len(segs)} segment{'s' if len(segs) != 1 else ''}>" if host else "unparseable"


def make_card(row: dict) -> dict:
    return {
        "name": (row.get("name") or "")[: CAPS["name"]],
        "symbol": (row.get("symbol") or "")[: CAPS["symbol"]],
        "description": (row.get("description") or "")[: CAPS["description"]],
        "twitter": link_shape(row.get("twitter", "")),
        "telegram": link_shape(row.get("telegram", "")),
        "website": link_shape(row.get("website", "")),
    }


def build(rows: list[dict], n: int = N_CARDS) -> tuple[list[dict], dict[str, str]]:
    ok = [r for r in rows if r.get("status") == "ok" and r.get("host_class") == "ipfs_cid"
          and r.get("s0c_json_ok") and r.get("name") is not None]
    ok.sort(key=lambda r: hashlib.sha256(("P28H-CARDS" + r["mint"]).encode()).hexdigest())
    pick = ok[:n]
    pick.sort(key=lambda r: hashlib.sha256(("P28H-ORDER" + r["mint"]).encode()).hexdigest())
    cards, mp = [], {}
    for i, r in enumerate(pick, 1):
        cid = f"c{i:02d}"
        cards.append({"id": cid, **make_card(r)})
        mp[cid] = r["mint"]
    return cards, mp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/data/mal/hunt-1008/push-1016/hunt30/p28h/stage0d")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    rows = [json.loads(x) for x in (d / "fetched.jsonl").read_text().splitlines() if x.strip()]
    cards, mp = build(rows)
    (d / "debug_cards.json").write_text(json.dumps({"cards": cards}, indent=1, ensure_ascii=False))
    (d / "debug_map.json").write_text(json.dumps(mp, indent=1, sort_keys=True))
    print(json.dumps({"cards": len(cards), "eligible_rows": sum(
        1 for r in rows if r.get("status") == "ok" and r.get("s0c_json_ok"))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
