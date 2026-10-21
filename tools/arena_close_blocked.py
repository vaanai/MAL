#!/usr/bin/env python3
"""arena_close_blocked: traded mints of an arena book -> the position_id list that `tools/arena_report_legs.py --close-blocked` reads (DEC-031 Am.2 item 6),
plus a cheap per-mint pre-filter the live bridge can call before a buy (READINESS spec S3). PAPER / structure only: no key is read here except through
`tools.mint_account_fetch` in the `fetch` mode, no fill, price or P&L is loaded, nothing is sent.

It WRAPS tools/mint_account_fetch.py (its behaviour is unchanged; other tools pin it). The mint-account record is the r4b schema that `members` writes:
    {fetch_slot, mints: {mint: {fetch_ok, owner_program, freeze_authority, token2022_extensions}}}

Close-blocked rule (Am.2 item 6, deny by default): a mint is close-blocked if the fetch failed (after the retries), or it has a freeze authority, or its owner is
neither SPL Token nor Token-2022, or it is Token-2022 with any extension outside {metadataPointer, tokenMetadata}, or the record is missing or malformed.
A position whose entry row names no mint (and no pool->mint map resolves it) is close-blocked too and counted as `unresolved`, never dropped.

Decision-log fields read (only these): kind, order_id, pool, and a mint from meta.mint / meta.child_mint / card.mint. The hash chain is verified first
(a broken chain is refused). Mode summary
  from-r4b  offline: --mint-accounts mint_accounts.json --decisions-root R --bot BOT --out-dir D
  fetch     network (Helius env as mint_account_fetch): same, but fetches the traded mints first; up to 3 rounds, all within 24 h of the first attempt.
Outputs in --out-dir: close_blocked.json (sorted JSON list of position_ids; feeds --close-blocked unmodified) and close_blocked_summary.json (counts, reasons,
credits, unresolved). fetch also writes mint_accounts.json.

Live pre-filter: `LivePrefilter(records=..., fetcher=..., ttl_s=...).check(mint)` returns a `Verdict`; a cached or preloaded mint costs no call.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from tools import mint_account_fetch as MF
from tools.arena_declog import DecisionLog, LogChainError

SPL_TOKEN_PROGRAM = MF.SPL_TOKEN_PROGRAM
TOKEN_2022_PROGRAM = MF.TOKEN_2022_PROGRAM
EXT_ALLOWLIST = frozenset({"metadataPointer", "tokenMetadata"})
# same list as tools/arena_report_legs.py FORBIDDEN_PARTS (copied, as that tool copies arena_feed_delay's)
FORBIDDEN_PARTS = ("sealed", "forward", "shadow", "canary", "probe", "walk", "exp011", "exp012", "tip-tape", "meta-capture", "fresh-08", "truth-", "_shakedown")
RETRY_ROUNDS = 3
RETRY_WINDOW_S = 24 * 3600


class Refused(Exception):
    pass


def check_text(text: str, what: str) -> None:
    low = text.lower()
    for part in FORBIDDEN_PARTS:
        if part in low:
            raise Refused(f"{what} contains {part!r}: this tool never reads sealed, forward, walk, probe, shadow, canary, shakedown or other closed material")


def check_path(path: str | os.PathLike, what: str = "path") -> Path:
    check_text(os.path.abspath(os.fspath(path)), f"{what} {os.fspath(path)!r}")
    check_text(os.path.realpath(os.fspath(path)), f"{what} {os.fspath(path)!r} (resolved)")
    return Path(path)


# ------------------------------------------------------------------------------------------------------------------------------ classification
def classify_record(rec: Any, allow: frozenset = EXT_ALLOWLIST) -> str | None:
    """None if the mint is NOT close-blocked; otherwise the reason. Deny by default."""
    if not isinstance(rec, Mapping):
        return "missing_record"
    if rec.get("fetch_ok") is not True:
        return "fetch_failed"
    if rec.get("freeze_authority"):
        return "freeze_authority"
    owner = rec.get("owner_program")
    if owner == SPL_TOKEN_PROGRAM:
        return None
    if owner == TOKEN_2022_PROGRAM:
        ext = rec.get("token2022_extensions")
        if not isinstance(ext, list) or not all(isinstance(x, str) for x in ext):
            return "malformed_extensions"
        return None if all(x in allow for x in ext) else "extension_outside_allowlist"
    return "unknown_owner_program"


def load_r4b(path: str | os.PathLike) -> dict[str, Any]:
    """Read a mint_accounts.json (r4b schema) -> {mint: record}. Refuses a file of the wrong shape."""
    doc = json.loads(check_path(path, "mint-accounts file").read_text())
    mints = doc.get("mints") if isinstance(doc, dict) else None
    if not isinstance(mints, dict):
        raise Refused(f"{path}: not the r4b schema (expected {{fetch_slot, mints: {{mint: record}}}})")
    return mints


# ------------------------------------------------------------------------------------------------------------------------------ decision log -> positions
def _mint_of(row: Mapping[str, Any]) -> str | None:
    for container, key in (("meta", "mint"), ("meta", "child_mint"), ("card", "mint")):
        c = row.get(container)
        v = c.get(key) if isinstance(c, Mapping) else None
        if isinstance(v, str) and v:
            return v
    return None


def positions_from_declog(root: str | os.PathLike, bot_id: str) -> list[dict[str, Any]]:
    """[{position_id, pool, mint|None}] from the `entry` rows of one bot's decision log. The chain is verified first (LogChainError -> Refused).
    Only kind / order_id / pool / mint fields are used. A position id is listed once."""
    check_text(bot_id, "bot_id")
    check_path(root, "decisions root")
    log = DecisionLog(root)
    if not log.files(bot_id):
        raise Refused(f"no decision log for bot {bot_id!r} under {root}")
    try:
        log.verify(bot_id)
    except LogChainError as e:
        raise Refused(f"decision-log chain broken: {e}") from None
    out: dict[str, dict[str, Any]] = {}
    for row in log.rows(bot_id):
        if row.get("kind") != "entry" or not isinstance(row.get("order_id"), str):
            continue
        out.setdefault(row["order_id"], {"position_id": row["order_id"], "pool": row.get("pool"), "mint": _mint_of(row)})
    return list(out.values())


def traded_mints(positions: Iterable[Mapping[str, Any]], pool_mints: Mapping[str, str] | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    """(sorted distinct mints, positions with the mint filled in where a pool->mint map resolves it; mint None stays None)."""
    pm = pool_mints or {}
    res = []
    for p in positions:
        q = dict(p)
        if not q.get("mint") and q.get("pool") in pm:
            q["mint"] = pm[q["pool"]]
        res.append(q)
    return sorted({q["mint"] for q in res if q.get("mint")}), res


def close_blocked_ids(positions: Iterable[Mapping[str, Any]], records: Mapping[str, Any], allow: frozenset = EXT_ALLOWLIST) -> tuple[list[str], dict[str, Any]]:
    """(sorted position_ids that are close-blocked, summary). A position with no mint is close-blocked ('unresolved'); a mint absent from records is 'missing_record'."""
    ids: list[str] = []
    reasons: dict[str, int] = {}
    blocked_mints: set[str] = set()
    n = 0
    unresolved = 0
    for p in positions:
        n += 1
        mint = p.get("mint")
        if not mint:
            why = "unresolved_mint"
            unresolved += 1
        else:
            why = classify_record(records.get(mint), allow)
            if why:
                blocked_mints.add(mint)
        if why:
            ids.append(p["position_id"])
            reasons[why] = reasons.get(why, 0) + 1
    return sorted(set(ids)), {"positions": n, "close_blocked_positions": len(set(ids)), "close_blocked_mints": len(blocked_mints), "unresolved": unresolved,
                              "reasons": dict(sorted(reasons.items())), "allowlist": sorted(allow)}


# ------------------------------------------------------------------------------------------------------------------------------ fetch with retries
def fetch_records(fetcher: MF.Fetcher, mints: list[str], rounds: int = RETRY_ROUNDS, window_s: float = RETRY_WINDOW_S, gap_s: float = 3600.0,
                  sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, dict], dict[str, Any]]:
    """Fetch every mint with mint_account_fetch.fetch_one; a mint that failed is retried (same rule for every mint) in up to `rounds` rounds in total, each
    started within `window_s` of the first attempt. Returns ({mint: full record}, {rounds_used, calls, credits_approx, still_failed})."""
    t0 = clock()
    recs: dict[str, dict] = {}
    todo = list(mints)
    used = 0
    for r in range(rounds):
        if not todo:
            break
        if r > 0:
            if clock() - t0 + gap_s > window_s:
                break
            sleep(gap_s)
        used += 1
        for m in todo:
            recs[m] = MF.fetch_one(fetcher, m)
        todo = [m for m in todo if not recs[m]["fetch_ok"]]
    return recs, {"rounds_used": used, "calls": fetcher.calls, "credits_approx": fetcher.calls, "still_failed": len(todo)}


def to_r4b(recs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    slots = [r["fetch_slot"] for r in recs.values() if r.get("fetch_ok")]
    return {"fetch_slot": max(slots) if slots else None,
            "mints": {m: {k: r.get(k) for k in ("fetch_ok", "owner_program", "freeze_authority", "token2022_extensions")} for m, r in sorted(recs.items())}}


# ------------------------------------------------------------------------------------------------------------------------------ live pre-filter
@dataclass(frozen=True)
class Verdict:
    mint: str
    blocked: bool
    reason: str | None
    source: str  # "preloaded" | "cache" | "fetch"
    calls: int = 0


class LivePrefilter:
    """Per-mint check at decision time. Preloaded records (a mint_accounts.json) and the cache cost no call. A good record is cached `ttl_s` (default 1 h); a
    failed fetch is blocked (deny by default) and cached only `fail_ttl_s` (default 30 s) so the next decision may retry. Not thread safe (one bridge loop)."""

    def __init__(self, records: Mapping[str, Any] | None = None, fetcher: MF.Fetcher | None = None, ttl_s: float = 3600.0, fail_ttl_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic, allow: frozenset = EXT_ALLOWLIST) -> None:
        self.pre = dict(records or {})
        self.fetcher, self.ttl, self.fail_ttl, self.clock, self.allow = fetcher, ttl_s, fail_ttl_s, clock, allow
        self.cache: dict[str, tuple[float, str | None]] = {}

    def check(self, mint: str | None) -> Verdict:
        if not mint or not isinstance(mint, str):
            return Verdict(str(mint), True, "unresolved_mint", "preloaded")
        if mint in self.pre:
            return Verdict(mint, (r := classify_record(self.pre[mint], self.allow)) is not None, r, "preloaded")
        hit = self.cache.get(mint)
        if hit and self.clock() < hit[0]:
            return Verdict(mint, hit[1] is not None, hit[1], "cache")
        if self.fetcher is None:  # no fetcher and no record: deny
            return Verdict(mint, True, "missing_record", "preloaded")
        before = self.fetcher.calls
        rec = MF.fetch_one(self.fetcher, mint)
        why = classify_record(rec, self.allow)
        self.cache[mint] = (self.clock() + (self.ttl if rec["fetch_ok"] else self.fail_ttl), why)
        return Verdict(mint, why is not None, why, "fetch", self.fetcher.calls - before)


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def build(bot_id: str, decisions_root: str, records: Mapping[str, Any], pool_mints: Mapping[str, str] | None = None) -> tuple[list[str], dict[str, Any]]:
    positions = positions_from_declog(decisions_root, bot_id)
    mints, positions = traded_mints(positions, pool_mints)
    ids, summ = close_blocked_ids(positions, records)
    summ.update({"bot_id": bot_id, "traded_mints": len(mints), "mints_without_record": sum(1 for m in mints if m not in records), "outcome_blind": True})
    return ids, summ


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["from-r4b", "fetch"])
    ap.add_argument("--bot", required=True)
    ap.add_argument("--decisions-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--mint-accounts", help="from-r4b: the mint_accounts.json (r4b schema)")
    ap.add_argument("--pool-mints", help="optional JSON {pool: mint} for entry rows that carry no mint")
    ap.add_argument("--env-file", type=Path, default=MF.HELIUS_ENV_FILE)
    ap.add_argument("--retry-gap-s", type=float, default=3600.0)
    a = ap.parse_args(argv)
    try:
        check_text(a.bot, "bot_id")
        out = check_path(a.out_dir, "out dir")
        pm = json.loads(check_path(a.pool_mints, "pool-mints file").read_text()) if a.pool_mints else None
        extra: dict[str, Any] = {}
        if a.mode == "from-r4b":
            if not a.mint_accounts:
                raise Refused("from-r4b needs --mint-accounts")
            records = load_r4b(a.mint_accounts)
        else:
            mints, _ = traded_mints(positions_from_declog(a.decisions_root, a.bot), pm)
            try:
                key = MF.load_key(env_file=a.env_file)
            except MF.FetchError as e:
                print("error:", e, file=sys.stderr)
                return 2
            full, extra = fetch_records(MF.Fetcher(MF.rpc_url(key), secrets=[key]), mints, gap_s=a.retry_gap_s)
            records = to_r4b(full)["mints"]
            out.mkdir(parents=True, exist_ok=True)
            (out / "mint_accounts.json").write_text(json.dumps(to_r4b(full), indent=1, sort_keys=True) + "\n")
        ids, summ = build(a.bot, a.decisions_root, records, pm)
        summ.update(extra)
    except (Refused, OSError, ValueError) as e:
        print("refused:", e, file=sys.stderr)
        return 3
    out.mkdir(parents=True, exist_ok=True)
    (out / "close_blocked.json").write_text(json.dumps(ids) + "\n")
    (out / "close_blocked_summary.json").write_text(json.dumps(summ, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: summ[k] for k in ("positions", "close_blocked_positions", "close_blocked_mints", "unresolved")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
