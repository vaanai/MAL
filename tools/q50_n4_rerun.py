"""Q50 C0 canary N4 rerun (DEC-032 s4 / C7; QP-PRERULING-1026-n2-n4 N4): sell + base-ATA-close simulate on two mints OUTSIDE the Look-2 class.

Keyless and read-only: public RPC only, unsigned simulateTransaction (sigVerify false, via tools/arena_sell_simulate.holder_run), holder mode, the
book's own send shape (priority profile `default`, 55,000/55,000/150,000). The sell builder is the PINNED one (ax.sell_pin_problem() must be None).
Nothing is signed or sent. No proceeds, min_out or balances are printed.

Mint rule (pre-ruling): each pool's s0 (oldest signature blockTime) must be in September 2026, chosen from the pool's create fact only (never by
outcome, V or an H5 rule, never from an October hour). The caller gives s0 and its source; the script also tries a public-RPC oldest-signature walk
(capped) and refuses on a contradiction. One Token and one Token-2022 mint. Pass = full_sell_ok and control_reverts_at_base_close for both.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools import arena_sell_simulate as ss
from tools import h5_arena_executor as ax

SEPT_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
SEPT_END = datetime(2026, 10, 1, tzinfo=timezone.utc)  # exclusive; the Look-2 class starts on October hours
MAX_PAGES = 30


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def s0_problem(s0: datetime) -> str | None:
    return None if SEPT_START <= s0 < SEPT_END else f"s0_not_september:{s0.isoformat()}"


def oldest_block_time(rpc: Callable[[str, list], Any], pool: str, max_pages: int = MAX_PAGES) -> tuple[int | None, bool]:
    """(oldest blockTime seen, reached_end). Walks getSignaturesForAddress backwards; reached_end False means the cap stopped it."""
    before, oldest = None, None
    for _ in range(max_pages):
        cfg: dict[str, Any] = {"limit": 1000}
        if before:
            cfg["before"] = before
        rows = rpc("getSignaturesForAddress", [pool, cfg]) or []
        if not rows:
            return oldest, True
        before, oldest = rows[-1]["signature"], rows[-1].get("blockTime", oldest)
        if len(rows) < 1000:
            return oldest, True
    return oldest, False


def check_one(rpc, kind: str, mint: str, pool: str, s0_utc: str, s0_source: str, expect_program: str, out_dir: Path) -> dict[str, Any]:
    s0 = parse_utc(s0_utc)
    prob = s0_problem(s0)
    if prob:
        return {"kind": kind, "mint": mint, "pool": pool, "ok": False, "refusal": prob}
    oldest, reached = oldest_block_time(rpc, pool)
    if reached and oldest is not None and not (SEPT_START.timestamp() <= oldest < SEPT_END.timestamp()):
        return {"kind": kind, "mint": mint, "pool": pool, "ok": False, "refusal": f"rpc_oldest_not_september:{oldest}"}
    rec = ss.holder_run(rpc, mint, pool, ax.PRIORITY_DEFAULT)
    out = out_dir / f"q50-n4-{kind}-{mint[:8]}.json"
    sha = ss.write_record(rec, out)
    v = rec["verdict"]
    ok = bool(v["full_sell_ok"]) and bool(v["control_reverts_at_base_close"]) and rec["token_program"] == expect_program
    return {"kind": kind, "mint": mint, "pool": pool, "s0_utc": s0_utc, "s0_source": s0_source, "rpc_oldest_blocktime": oldest, "rpc_oldest_reached_end": reached,
            "token_program": rec["token_program"], "expected_program": expect_program, "full_sell_ok": v["full_sell_ok"],
            "control_reverts_at_base_close": v["control_reverts_at_base_close"],
            "shape": rec["runs"]["full_balance"]["shape"], "record": str(out), "record_sha256": sha, "ok": ok}


def main(argv: list[str] | None = None, rpc_factory: Callable[[str], Any] | None = None) -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    for k in ("token", "token2022"):
        ap.add_argument(f"--{k}-mint", required=True)
        ap.add_argument(f"--{k}-pool", required=True)
        ap.add_argument(f"--{k}-s0-utc", required=True, help="pool create blockTime, UTC, YYYY-MM-DDTHH:MM:SSZ, September 2026")
        ap.add_argument(f"--{k}-s0-source", required=True, help="where s0 came from: sept-audit-tape-migrations | rpc-oldest-signature")
    ap.add_argument("--rpc", default=ss.DEFAULT_RPC)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args(argv)
    if "?" in a.rpc or "api-key" in a.rpc.lower() or "@" in a.rpc:
        raise SystemExit("--rpc must be a plain public URL (no query string, no key)")
    pin = ax.sell_pin_problem()
    if pin:
        print(json.dumps({"verdict": "REFUSED", "refusal": f"sell_builder_pin:{pin}"}))
        return 2
    rpc = (rpc_factory or ss.RateLimitedRpc)(a.rpc)
    res = [check_one(rpc, "token", a.token_mint, a.token_pool, a.token_s0_utc, a.token_s0_source, "Token", a.out_dir),
           check_one(rpc, "token2022", a.token2022_mint, a.token2022_pool, a.token2022_s0_utc, a.token2022_s0_source, "Token-2022", a.out_dir)]
    summary = {"kind": "q50_n4_rerun.v1", "builder_pin": ax.SELL_BUILDER_PIN, "builder_pin_problem": None, "priority": ax.PRIORITY_DEFAULT.as_dict(),
               "rpc_host": a.rpc.split("/")[2], "signed": False, "results": res, "verdict": "PASS" if all(r["ok"] for r in res) else "FAIL",
               "not_measured": ["proceeds", "min_out", "landing", "any signed or sent transaction"]}
    sha = ss.write_record(summary, a.out_dir / "q50-n4-summary.json")
    print(json.dumps({"verdict": summary["verdict"], "summary_sha256": sha, "results": [{k: r.get(k) for k in ("kind", "mint", "ok", "refusal", "token_program")} for r in res]}))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
