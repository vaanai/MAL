"""BOOST slice watch: daily, REPORT-ONLY structure measurement of PumpSwap BOOST slices per pool.

Run: python -m tools.boost_slice_watch --record /data/mal/structure-monitor/daily.jsonl --out PATH
     python -m tools.boost_slice_watch --slot-range MIN-MAX [--slot-range MIN-MAX ...] --out PATH [--no-exact]

What it is for. On 2026-10-13 the owner decided that the BOOST "slices per pool: 29-30" band is read on the median, and
asked for two things to be tracked daily, report-only: (a) the share of pools under 29 slices, and (b) any pause between
slices of 60 seconds or more. This tool measures both. It states NO verdict and NO flag: the comparison with a baseline
is done by the manager under a rule that is not part of this tool.

Report-only. It never halts anything, never writes a STOP, never reads a key, a tape, a ledger, a shadow file or any
outcome. Its only inputs are public-RPC chain structure and (with --record) the last line of a monitor JSONL file, from
which it copies two slot numbers and run_utc. Keyed and Helius RPC URLs are refused. Pacing is polite, retries are
bounded, and --max-calls is a hard cap: at the cap the run stops, writes the partial record with status "stopped" and
exits 3. It never samples silently.

Sample. Every successful migrate transaction on the migration fee account with slot in [min, max] (inclusive), taken
the way the daily monitor reads one (tools/pump_structure_monitor.py: parse_migrate_tx, fetch_tx, annotate_mayhem and the
RPC client are reused, not copied), kept when it is non-mayhem, WSOL-quoted and carries InitBoost. The finder itself
(sample_range_sigs, find_migrations) is NEW code: the monitor's sample_graduations takes the N newest by block time, not a
slot range. The tool stops (status "stopped") if GlobalConfig is not the 949-byte layout; the ad-hoc scripts read the keeper
bytes regardless, so this is safer, not equal. Anything older than the range is paged past; run it soon after the monitor record,
because each day of age adds signature pages.

Two slice counts, do not mix them.
  sig_count             The signature-list count: every successful transaction on the pool's boost vault authority
                        except the migrate, capped at 100 listed entries. It is an UPPER BOUND: it can include 0-2 non-keeper transactions per pool
                        (2026-10-08 check). It is the monitor's own "slices" statistic.
  exact_keeper_slices   (--exact) Every transaction in that list is fetched. A slice is a BoostBuyAndBurn instruction
                        signed by GlobalConfig.boost_authority (the keeper). Non-keeper transactions are excluded.
                        zero_fee: the vault's WSOL change equals minus quote_amount_in_used and exactly one other
                        account gained exactly that amount. A pool is "fully fetched" only if its list is not
                        truncated at 100, its migrate transaction is the oldest entry, and every listed transaction
                        was fetched. A pool that is not fully fetched has its count reported but is NOT counted in the
                        exact tallies; it is counted in n_not_fully_fetched.

Gaps. largest_gap_s is measured on SIGNATURE-LIST times (every successful transaction on the vault authority except the
migrate, sorted by block time), not on verified keeper slices. A non-keeper transaction inside a pause therefore HIDES it
from largest_gap_s. In exact mode largest_keeper_gap_s (keeper slice times, fully fetched pools) is the check for that;
summary n_keeper_gap_ge_60 counts it. last_slice_s is the block time of the newest keeper-signed
BoostBuyAndBurn among the 8 newest listed transactions, minus the migrate block time (same rule as the monitor); None
when none of the 8 qualifies.

Reference readings recorded on 2026-10-13 with two ad-hoc scripts (not by this tool):
  pre-upgrade  (50 pools of the four A3 runs 2026-10-09..10-12): sig_count <= 28 on 6; largest gap >= 60 s on 0.
  post-upgrade (40 pools): sig_count <= 28 on 2; exact under 29 on 4 of 39 fully fetched; largest gap >= 60 s on 1.

Nothing is dropped silently. Every drop is a count in the record: n_candidate_no_blocktime, n_migrate_tx_null,
n_no_block_time (eligible graduation without a block time), per pool n_sig_no_blocktime (such a pool is not fully
fetched), truncated / n_truncated_at_100 (list hit 100 entries; in either mode). n_migrate_found = n_profiled +
n_no_block_time + (graduations excluded as mayhem, unknown-mayhem, not WSOL or without InitBoost; those four overlap).
A "stopped" record, or ANY nonzero drop count, is not a reading: read status, calls and the drop counts first.

Exit codes: 0 done, 3 stopped (call cap or unrecoverable RPC failure; partial record written), 2 usage error.
Output is structure data only: no price, fill or outcome field exists in it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from observe.trade_decode import decode_extra_event
from tools import pump_structure_monitor as m

SCHEMA = "boost_slice_watch.v1"
DEFAULT_MAX_CALLS = 5000
DEFAULT_MIN_INTERVAL = 0.6
SIG_PAGE = 1000
MAX_PAGES = 30
LIST_LIMIT = 100  # signature list per pool; a full page means truncated
VERIFY_FETCHES = 8
GAP_PAUSE_S = 60
LAST_LATE_S = 400
UNDER = 29  # exact slices under this are "under 29"
OVER = 30
ROUGH_LE = 28

KEY_QUERY_HINTS = ("api-key", "api_key", "apikey", "key=", "token=", "access_token")


class Stop(Exception):
    """The run cannot continue; the partial record is written with status stopped."""


def refuse_reason(url: str) -> str | None:
    """Why this RPC URL is refused, or None. Public RPC only: no Helius, no key in userinfo or query."""
    low = url.lower()
    if "helius" in low:
        return "Helius URLs are refused (public RPC only)"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "unparseable RPC URL"
    if parsed.username or parsed.password:
        return "RPC URL carries credentials"
    if any(h in (parsed.query or "").lower() for h in KEY_QUERY_HINTS):
        return "RPC URL carries a key in its query"
    return None


def parse_range(text: str) -> tuple[int, int]:
    lo_s, sep, hi_s = text.partition("-")
    if not sep:
        raise argparse.ArgumentTypeError(f"slot range must be MIN-MAX, got {text!r}")
    try:
        lo, hi = int(lo_s), int(hi_s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"slot range must be MIN-MAX integers, got {text!r}") from None
    if lo < 0 or hi < lo:
        raise argparse.ArgumentTypeError(f"slot range needs 0 <= MIN <= MAX, got {text!r}")
    return lo, hi


def read_record_range(path: Path) -> tuple[tuple[int, int], str | None]:
    """((slot_min, slot_max), run_utc) from the LAST non-empty line of a monitor JSONL file."""
    last = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                last = line
    if last is None:
        raise ValueError(f"{path}: no records")
    rec = json.loads(last)
    grads = rec.get("graduations") if isinstance(rec, dict) else None
    lo, hi = (grads or {}).get("slot_min"), (grads or {}).get("slot_max")
    if not isinstance(lo, int) or not isinstance(hi, int) or isinstance(lo, bool) or isinstance(hi, bool):
        raise ValueError(f"{path}: last record has no graduations.slot_min / slot_max")
    return (lo, hi), rec.get("run_utc")


# =============================================================================================
# per-pool measurement
# =============================================================================================
class Fetcher:
    """getTransaction with a per-run cache, so the last-slice check and the exact pass never fetch a signature twice."""

    def __init__(self, client: m.RpcClient) -> None:
        self.client = client
        self.cache: dict[str, Any] = {}

    def tx(self, sig: str) -> Any:
        if sig not in self.cache:
            self.cache[sig] = m.fetch_tx(self.client, sig)
        return self.cache[sig]


def is_keeper_slice(tx: Mapping[str, Any], authority: str) -> bool:
    return authority in m.tx_signers(tx) and "BoostBuyAndBurn" in m.program_instructions(m.tx_logs(tx), m.PUMPSWAP_PROGRAM)


def wsol_changes(tx: Mapping[str, Any]) -> dict[str, int]:
    meta = tx.get("meta") or {}
    keys = m.tx_keys(tx)

    def amounts(rows: Any) -> dict[int, int]:
        return {q["accountIndex"]: int(q["uiTokenAmount"]["amount"]) for q in rows or [] if q.get("mint") == m.WSOL_MINT}

    pre, post = amounts(meta.get("preTokenBalances")), amounts(meta.get("postTokenBalances"))
    return {keys[i]: post.get(i, 0) - pre.get(i, 0) for i in set(pre) | set(post) if post.get(i, 0) != pre.get(i, 0) and i < len(keys)}


def slice_row(tx: Mapping[str, Any], vault: str, authority: str) -> dict[str, Any]:
    ev = None
    for raw in m.tx_event_blobs(tx):
        e = decode_extra_event(raw)
        if e and e["type"] == "boost_buy_and_burn":
            ev = e
    used = ev["quote_amount_in_used"] if ev else None
    ch = wsol_changes(tx)
    others = {k: v for k, v in ch.items() if k != vault and v > 0}
    return {
        "bt": tx.get("blockTime"),
        "bbb": "BoostBuyAndBurn" in m.program_instructions(m.tx_logs(tx), m.PUMPSWAP_PROGRAM),
        "signed": authority in m.tx_signers(tx),
        "zero_fee": bool(used is not None and ch.get(vault) == -used and len(ch) == 2 and len(others) == 1 and sum(others.values()) == used),
    }


def exact_pool(fetch: Fetcher, g: Mapping[str, Any], sigs: Sequence[Mapping[str, Any]], authority: str, n_no_bt: int) -> dict[str, Any]:
    ordered = sorted(sigs, key=lambda x: (x["slot"], x.get("transactionIndex") or 0))
    truncated = len(sigs) >= LIST_LIMIT
    mig_identified = bool(ordered) and ordered[0]["signature"] == g["sig"] and ordered[0].get("err") is None
    rest = [x for x in ordered if x.get("err") is None and x.get("blockTime") is not None and x["signature"] != g["sig"]]
    vault = m.boost_vault(g["pool"])
    rows, missing = [], 0
    for x in rest:
        try:
            tx = fetch.tx(x["signature"])
        except m.RpcError:
            tx = None  # a definitive per-transaction failure: the pool is then not fully fetched
        if not tx:
            missing += 1
            continue
        rows.append(slice_row(tx, vault, authority))
    bbb = [r for r in rows if r["bbb"]]
    keeper = [r for r in bbb if r["signed"]]
    kt = sorted(r["bt"] for r in keeper if r["bt"] is not None)
    kgaps = [b - a for a, b in zip(kt, kt[1:])]
    return {
        "exact_keeper_slices": len(keeper),
        "keeper_signed": len(keeper),
        "zero_fee": sum(1 for r in keeper if r["zero_fee"]),
        "non_keeper_tx": len(rows) - len(keeper),
        "bbb_not_keeper_signed": len(bbb) - len(keeper),
        "largest_keeper_gap_s": max(kgaps) if kgaps else 0,
        "fully_fetched": (not truncated) and mig_identified and missing == 0 and n_no_bt == 0,
        "migrate_identified": mig_identified,
        "truncated": truncated,
        "n_missing_tx": missing,
    }


def profile_pool(fetch: Fetcher, g: Mapping[str, Any], authority: str, exact: bool) -> dict[str, Any]:
    sigs = fetch.client.call("getSignaturesForAddress", [m.boost_vault_authority(g["pool"]), {"limit": LIST_LIMIT, "commitment": "finalized"}]) or []
    sl = [x for x in sigs if x.get("err") is None and x["signature"] != g["sig"] and x.get("blockTime") is not None]
    times = sorted(x["blockTime"] for x in sl)
    n_no_bt = sum(1 for x in sigs if x.get("err") is None and x["signature"] != g["sig"] and x.get("blockTime") is None)
    gaps = [b - a for a, b in zip(times, times[1:])]
    sl.sort(key=lambda x: (x["slot"], x.get("transactionIndex") or 0), reverse=True)
    last_t = None
    for x in sl[:VERIFY_FETCHES]:
        tx = fetch.tx(x["signature"])
        if tx and is_keeper_slice(tx, authority):
            last_t = x["blockTime"]
            break
    out: dict[str, Any] = {
        "pool": g["pool"],
        "migrate_slot": g["slot"],
        "sig_count": len(times),
        "truncated": len(sigs) >= LIST_LIMIT,
        "n_sig_no_blocktime": n_no_bt,
        "largest_gap_s": max(gaps) if gaps else 0,
        "last_slice_s": (last_t - g["block_time"]) if last_t is not None else None,
    }
    if exact:
        out.update(exact_pool(fetch, g, sigs, authority, n_no_bt))
    return out


# =============================================================================================
# sampling
# =============================================================================================
def sample_range_sigs(client: m.RpcClient, ranges: Sequence[tuple[int, int]]) -> list[Mapping[str, Any]]:
    lo = min(r[0] for r in ranges)
    sigs: list[Mapping[str, Any]] = []
    before = None
    for _ in range(MAX_PAGES):
        opts: dict[str, Any] = {"limit": SIG_PAGE, "commitment": "finalized"}
        if before:
            opts["before"] = before
        page = client.call("getSignaturesForAddress", [m.MIGRATION_FEE_ACCOUNT, opts]) or []
        sigs += page
        if len(page) < SIG_PAGE or page[-1]["slot"] < lo:
            return sigs
        before = page[-1]["signature"]
    raise Stop(f"paging did not reach slot {lo} within {MAX_PAGES} pages")


def find_migrations(fetch: Fetcher, sigs: Sequence[Mapping[str, Any]], lo: int, hi: int, seen: set[str], counts: dict[str, int]) -> list[dict[str, Any]]:
    """An RpcError on a candidate propagates (the run stops); null transactions and null blockTimes are counted."""
    grads = []
    for s in sigs:
        if not (lo <= s["slot"] <= hi) or s.get("err") is not None:
            continue
        if s.get("blockTime") is None:
            counts["n_candidate_no_blocktime"] += 1
            continue
        tx = fetch.tx(s["signature"])
        if not tx:
            counts["n_migrate_tx_null"] += 1
            continue
        g = m.parse_migrate_tx(tx)
        if g is None or g["mint"] in seen:
            continue
        seen.add(g["mint"])
        g["sig"] = s["signature"]
        grads.append(g)
    return grads


def med(values: Sequence[float]) -> float | int | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def summarize(pools: Sequence[Mapping[str, Any]], exact: bool) -> dict[str, Any]:
    counts = [p["sig_count"] for p in pools]
    n_le = sum(1 for c in counts if c <= ROUGH_LE)
    out: dict[str, Any] = {
        "n_profiled": len(pools),
        "n_truncated_at_100": sum(1 for p in pools if p.get("truncated")),
        "n_sig_no_blocktime": sum(p.get("n_sig_no_blocktime", 0) for p in pools),
        "rough": {"n": len(pools), "n_le_28": n_le, "share_le_28": m.share(n_le, len(pools)), "min": min(counts, default=None), "median": med(counts), "max": max(counts, default=None)},
    }
    if exact:
        ff = [p for p in pools if p.get("fully_fetched")]
        ec = [p["exact_keeper_slices"] for p in ff]
        n_under = sum(1 for c in ec if c < UNDER)
        out["exact"] = {
            "n_fully_fetched": len(ff),
            "n_not_fully_fetched": sum(1 for p in pools if "fully_fetched" in p and not p["fully_fetched"]),
            "n_under_29": n_under,
            "share_under_29": m.share(n_under, len(ff)),
            "n_over_30": sum(1 for c in ec if c > OVER),
            "min": min(ec, default=None),
            "median": med(ec),
            "max": max(ec, default=None),
            "n_keeper_gap_ge_60": sum(1 for p in ff if p["largest_keeper_gap_s"] >= GAP_PAUSE_S),
            "n_slices": sum(ec),
            "n_keeper_signed": sum(p["keeper_signed"] for p in ff),
            "n_zero_fee": sum(p["zero_fee"] for p in ff),
            "n_bbb_not_keeper_signed": sum(p["bbb_not_keeper_signed"] for p in ff),
        }
    last = [p["last_slice_s"] for p in pools if p["last_slice_s"] is not None]
    wide = [p for p in pools if p["largest_gap_s"] >= GAP_PAUSE_S]
    out["gaps"] = {
        "n": len(pools),
        "n_gap_ge_60": len(wide),
        "n_last_gt_400": sum(1 for x in last if x > LAST_LATE_S),
        "n_last_verified": len(last),
        "last_slice_median_s": med(last),
        "pools_gap_ge_60": [{"pool": p["pool"], "largest_gap_s": p["largest_gap_s"]} for p in wide],
    }
    return out


def measure(client: m.RpcClient, ranges: Sequence[tuple[int, int]], exact: bool, pools: list[dict[str, Any]], counts: dict[str, int]) -> None:
    """Fill `pools` (completed pools only) and `counts` as it goes, so a stop keeps what was finished."""
    fetch = Fetcher(client)
    cfg = client.call("getAccountInfo", [m.PUMPSWAP_GLOBAL_CONFIG, {"encoding": "base64", "commitment": "finalized"}])
    entry = (cfg or {}).get("value")
    gc = m.decode_global_config(m._account_bytes(entry) or b"")
    authority = gc.get("boost_authority")
    if not authority or not gc.get("layout_ok"):
        raise Stop("GlobalConfig boost_authority unreadable or layout changed")
    sigs = sample_range_sigs(client, ranges)
    seen: set[str] = set()
    for lo, hi in ranges:
        grads = find_migrations(fetch, sigs, lo, hi, seen, counts)
        counts["n_migrate_found"] += len(grads)
        m.annotate_mayhem(client, grads)
        counts["n_mayhem"] += sum(1 for g in grads if g.get("mayhem") is True)
        counts["n_mayhem_unknown"] += sum(1 for g in grads if g.get("mayhem") is None)
        counts["n_not_wsol"] += sum(1 for g in grads if not g["quote_wsol"])
        counts["n_no_init_boost"] += sum(1 for g in grads if not g["init_boost"])
        for g in grads:
            if not (g["init_boost"] and g["quote_wsol"] and g.get("mayhem") is False):
                continue
            if not g.get("block_time"):
                counts["n_no_block_time"] += 1
                continue
            pools.append(profile_pool(fetch, g, authority, exact))


def run(client: m.RpcClient, ranges: Sequence[tuple[int, int]], *, exact: bool, now: float, record_run_utc: str | None) -> dict[str, Any]:
    pools: list[dict[str, Any]] = []
    counts = Counter({"n_migrate_found": 0, "n_mayhem": 0, "n_mayhem_unknown": 0, "n_not_wsol": 0, "n_no_init_boost": 0,
                      "n_candidate_no_blocktime": 0, "n_migrate_tx_null": 0, "n_no_block_time": 0})
    status, error, problem = "done", None, None
    try:
        measure(client, ranges, exact, pools, counts)  # type: ignore[arg-type]
    except (m.CallBudgetExceeded, m.RpcUnreachable, m.RpcError, Stop) as exc:
        status, error = "stopped", f"{type(exc).__name__}: {str(exc)[:200]}"
    except Exception as exc:  # any other failure: a visible stopped record, never a bare traceback with no record
        status, error, problem = "stopped", type(exc).__name__, type(exc).__name__
    rec: dict[str, Any] = {
        "schema": SCHEMA,
        "tool_run_utc": m.iso(now),
        "run_utc_of_record": record_run_utc,
        "slot_ranges": [list(r) for r in ranges],
        "exact_mode": exact,
        "status": status,
        "n_migrate_found": counts["n_migrate_found"],
        "n_mayhem": counts["n_mayhem"],
        "n_mayhem_unknown": counts["n_mayhem_unknown"],
        "n_not_wsol": counts["n_not_wsol"],
        "n_no_init_boost": counts["n_no_init_boost"],
        "n_candidate_no_blocktime": counts["n_candidate_no_blocktime"],
        "n_migrate_tx_null": counts["n_migrate_tx_null"],
        "n_no_block_time": counts["n_no_block_time"],
    }
    if error:
        rec["error"] = error
    if problem:
        rec["problem"] = problem
    rec.update(summarize(pools, exact))
    rec["calls"] = client.total_calls
    rec["pools"] = pools
    return rec


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BOOST slice watch (report-only; public RPC; structure data only).")
    p.add_argument("--slot-range", action="append", type=parse_range, metavar="MIN-MAX", help="inclusive slot range; repeatable")
    p.add_argument("--record", metavar="PATH", help="monitor JSONL file: the LAST line's graduations.slot_min/slot_max and run_utc are used")
    p.add_argument("--out", required=True, help="JSONL file to append one record to")
    p.add_argument("--exact", action=argparse.BooleanOptionalAction, default=True, help="fetch every slice transaction of every pool (default on)")
    p.add_argument("--rpc-url", default=m.DEFAULT_RPC, help="public mainnet RPC (default %(default)s); keyed or Helius URLs are refused")
    p.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help="hard cap on RPC calls, retries included (default %(default)s)")
    p.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL, help="seconds between RPC calls (default %(default)s)")
    args = p.parse_args(argv)
    if bool(args.slot_range) == bool(args.record):
        p.error("give either --slot-range (repeatable) or --record, not both and not neither")
    if args.max_calls < 1 or args.min_interval < 0:
        p.error("--max-calls must be >= 1 and --min-interval >= 0")
    return args


def summary_line(rec: Mapping[str, Any]) -> str:
    r, g = rec["rough"], rec["gaps"]
    parts = [f"boost_slice_watch status={rec['status']} calls={rec['calls']} migrate_found={rec['n_migrate_found']} profiled={rec['n_profiled']}",
             f"rough: le28={r['n_le_28']} share={r['share_le_28']} median={r['median']}"]
    if "exact" in rec:
        e = rec["exact"]
        parts.append(f"exact: fully_fetched={e['n_fully_fetched']} not_fully={e['n_not_fully_fetched']} under29={e['n_under_29']} share={e['share_under_29']} median={e['median']}")
    drops = {k: rec[k] for k in ("n_candidate_no_blocktime", "n_migrate_tx_null", "n_no_block_time", "n_truncated_at_100", "n_sig_no_blocktime") if rec.get(k)}
    if drops:
        parts.append(f"DROPS (not a reading): {drops}")
    parts.append(f"gaps: ge60={g['n_gap_ge_60']} last_gt_400={g['n_last_gt_400']} last_median={g['last_slice_median_s']}")
    return " | ".join(parts)


def main(argv: Sequence[str] | None = None, *, client: m.RpcClient | None = None, now: float | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    why = refuse_reason(args.rpc_url)
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    record_run_utc = None
    if args.record:
        try:
            rng, record_run_utc = read_record_range(Path(args.record))
        except (OSError, ValueError) as exc:
            print(f"usage error: {exc}", file=sys.stderr)
            return 2
        ranges = [rng]
    else:
        ranges = list(args.slot_range)
    if client is None:
        client = m.RpcClient(args.rpc_url, min_interval=args.min_interval, max_calls=args.max_calls)
    rec = run(client, ranges, exact=args.exact, now=time.time() if now is None else now, record_run_utc=record_run_utc)
    out = Path(args.out)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"WRITE FAILED {out}: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
        return 1
    print(summary_line(rec))
    return 0 if rec["status"] == "done" else 3


if __name__ == "__main__":
    sys.exit(main())
