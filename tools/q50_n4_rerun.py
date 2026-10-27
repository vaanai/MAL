"""Q50 C0 canary N4 rerun (DEC-032 s4 / C7; QP-PRERULING-1026-n2-n4 N4): sell + base-ATA-close simulate on two mints OUTSIDE the Look-2 class.

Keyless and read-only: public RPC only, unsigned simulateTransaction (sigVerify false, via tools/arena_sell_simulate.holder_run), holder mode, the
book's own send shape (priority profile `default`, 55,000/55,000/150,000). The sell builder is the PINNED one (ax.sell_pin_problem() must be None).
Nothing is signed or sent. No proceeds, min_out or balances are printed.

Mint rule (pre-ruling): each pool's s0 (oldest signature blockTime) must be in September 2026, chosen from the pool's create fact only (never by
outcome, V or an H5 rule, never from an October hour). The caller gives s0 and its source; the script also tries a public-RPC oldest-signature walk
(capped) and refuses on a contradiction. One Token and one Token-2022 mint. Pass = full_sell_ok and control_reverts_at_base_close for both.

Capped-walk hardening (QP-RULING-1027-n4-t22 (0)): when the walk hits its cap, the caller must give a migration slot and a tape or chain-migration
source, and also (1) getBlockTime(migration_slot) is within 120 s of the caller's s0, and (2) the oldest blockTime seen is >= s0 - 120 s and
< 2026-10-01T00:00:00Z.
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
from tools.boost_watch import rpc_from_env_file

SEPT_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
SEPT_END = datetime(2026, 10, 1, tzinfo=timezone.utc)  # exclusive; the Look-2 class starts on October hours
MAX_PAGES = 30
S0_TOLERANCE_S = 120  # |caller s0 - RPC oldest blockTime| when the walk reaches the pool's first signature
TAPE_SOURCE = "sept-audit-tape-migrations"
CHAIN_SOURCE = "chain-migration-tx"  # s0 = getBlockTime of an on-chain pump migration tx (QP (2) source ii)
CAPPED_SOURCES = (TAPE_SOURCE, CHAIN_SOURCE)


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


def s0_cross_check(s0: datetime, source: str, slot: int | None, oldest: int | None, reached: bool, slot_time: int | None = None) -> str | None:
    """Refusal reason or None. Walk capped: only a tape/chain source with a migration slot whose getBlockTime (slot_time) is within tolerance of s0,
    and an oldest blockTime seen in [s0 - tol, Oct 1). Walk complete: the found s0 must match."""
    if not reached:
        if source not in CAPPED_SOURCES or slot is None:
            return "walk_capped_needs_tape_source_with_migration_slot"
        if slot_time is None:
            return "slot_blocktime_missing"
        if abs(slot_time - s0.timestamp()) > S0_TOLERANCE_S:
            return f"slot_blocktime_mismatch:caller={int(s0.timestamp())},slot={slot_time},tol={S0_TOLERANCE_S}"
        if oldest is None:
            return "rpc_no_signatures"
        if oldest < s0.timestamp() - S0_TOLERANCE_S:
            return f"capped_oldest_before_s0:{oldest}"
        if oldest >= SEPT_END.timestamp():
            return f"capped_oldest_not_september:{oldest}"
        return None
    if oldest is None:
        return "rpc_no_signatures"
    if not (SEPT_START.timestamp() <= oldest < SEPT_END.timestamp()):
        return f"rpc_oldest_not_september:{oldest}"
    if abs(oldest - s0.timestamp()) > S0_TOLERANCE_S:
        return f"s0_mismatch:caller={int(s0.timestamp())},rpc={oldest},tol={S0_TOLERANCE_S}"
    return None


def check_one(rpc, kind: str, mint: str, pool: str, s0_utc: str, s0_source: str, expect_program: str, out_dir: Path, migration_slot: int | None = None) -> dict[str, Any]:
    s0 = parse_utc(s0_utc)
    prob = s0_problem(s0)
    if prob:
        return {"kind": kind, "mint": mint, "pool": pool, "ok": False, "refusal": prob}
    oldest, reached = oldest_block_time(rpc, pool)
    slot_time = None
    if not reached and migration_slot is not None and s0_source in CAPPED_SOURCES:
        slot_time = rpc("getBlockTime", [migration_slot])
    bad = s0_cross_check(s0, s0_source, migration_slot, oldest, reached, slot_time)
    if bad:
        return {"kind": kind, "mint": mint, "pool": pool, "ok": False, "refusal": bad}
    rec = ss.holder_run(rpc, mint, pool, ax.PRIORITY_DEFAULT)
    out = out_dir / f"q50-n4-{kind}-{mint[:8]}.json"
    sha = ss.write_record(rec, out)
    v = rec["verdict"]
    got = str(rec["token_program"]).lower()
    ok = bool(v["full_sell_ok"]) and bool(v["control_reverts_at_base_close"]) and got == expect_program.lower() and got in ss.TOKEN_NAMES.values()
    return {"kind": kind, "mint": mint, "pool": pool, "s0_utc": s0_utc, "s0_source": s0_source, "migration_slot": migration_slot, "rpc_oldest_blocktime": oldest, "rpc_oldest_reached_end": reached,
            "token_program": rec["token_program"], "expected_program": expect_program, "full_sell_ok": v["full_sell_ok"],
            "control_reverts_at_base_close": v["control_reverts_at_base_close"],
            "shape": rec["runs"]["full_balance"]["shape"], "record": str(out), "record_sha256": sha, "ok": ok}


def main(argv: list[str] | None = None, rpc_factory: Callable[[str], Any] | None = None) -> int:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    for k in ("token", "token2022"):
        ap.add_argument(f"--{k}-mint", required=True)
        ap.add_argument(f"--{k}-pool", required=True)
        ap.add_argument(f"--{k}-s0-utc", required=True, help="pool create blockTime, UTC, YYYY-MM-DDTHH:MM:SSZ, September 2026")
        ap.add_argument(f"--{k}-s0-source", required=True, help="sept-audit-tape-migrations | chain-migration-tx | rpc-oldest-signature")
        ap.add_argument(f"--{k}-migration-slot", type=int, default=None, help="required when the oldest-signature walk hits its cap")
    ap.add_argument("--rpc", default=ss.DEFAULT_RPC)
    ap.add_argument("--rpc-env", help="env file holding the Helius RPC URL or API key (e.g. /var/lib/mal/fast-listener/helius.env on mal-fast-0); the URL is built in-process and never printed or logged")
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args(argv)
    if "?" in a.rpc or "api-key" in a.rpc.lower() or "@" in a.rpc:
        raise SystemExit("--rpc must be a plain public URL (no query string, no key)")
    url, rpc_label = a.rpc, a.rpc.split("/")[2]
    if a.rpc_env:
        try:
            url, rpc_label = rpc_from_env_file(a.rpc_env), "helius"  # the only RPC identity that is ever logged
        except (OSError, ValueError) as exc:
            print(f"rpc-env unusable: {type(exc).__name__}", file=sys.stderr)  # type only: never the path contents
            return 2
    pin = ax.sell_pin_problem()
    if pin:
        print(json.dumps({"verdict": "REFUSED", "refusal": f"sell_builder_pin:{pin}"}))
        return 2
    rpc = (rpc_factory or ss.RateLimitedRpc)(url)
    res = [check_one(rpc, "token", a.token_mint, a.token_pool, a.token_s0_utc, a.token_s0_source, "Token", a.out_dir, a.token_migration_slot),
           check_one(rpc, "token2022", a.token2022_mint, a.token2022_pool, a.token2022_s0_utc, a.token2022_s0_source, "Token-2022", a.out_dir, a.token2022_migration_slot)]
    summary = {"kind": "q50_n4_rerun.v1", "builder_pin": ax.SELL_BUILDER_PIN, "builder_pin_problem": None, "priority": ax.PRIORITY_DEFAULT.as_dict(),
               "rpc_host": rpc_label, "signed": False, "results": res, "verdict": "PASS" if all(r["ok"] for r in res) else "FAIL",
               "not_measured": ["proceeds", "min_out", "landing", "any signed or sent transaction"]}
    sha = ss.write_record(summary, a.out_dir / "q50-n4-summary.json")
    print(json.dumps({"verdict": summary["verdict"], "summary_sha256": sha, "results": [{k: r.get(k) for k in ("kind", "mint", "ok", "refusal", "token_program")} for r in res]}))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
