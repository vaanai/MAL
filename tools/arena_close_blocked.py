#!/usr/bin/env python3
"""arena_close_blocked: traded mints of an arena book -> the position_id list that `tools/arena_report_legs.py --close-blocked` reads (DEC-031 Am.2 item 6),
plus a cheap per-mint pre-filter class the live bridge (S1) imports (READINESS spec S3; there is no `--live-prefilter` CLI mode, the class is the delivery).
PAPER / structure only: no key is read here except through `tools.mint_account_fetch` in the `fetch` mode, nothing is sent.

It WRAPS tools/mint_account_fetch.py (its behaviour is unchanged; other tools pin it). The mint-account record is the r4b schema that `members` writes:
    {fetch_slot, mints: {mint: {fetch_ok, owner_program, freeze_authority, token2022_extensions}}}

Close-blocked rule (Am.2 item 6, deny by default): a mint is close-blocked if the fetch failed (after the retries), or it has a freeze authority, or its owner is
neither SPL Token nor Token-2022, or it is Token-2022 with any extension outside {metadataPointer, tokenMetadata}, or the record is missing or malformed
(no `freeze_authority` key, or `token2022_extensions` not a list). A position whose entry row names no mint (and no pool->mint map resolves it) is close-blocked
too and counted as `unresolved`, never dropped.

WHICH POSITIONS. The output must be a subset of the read's covered trade set, or `arena_report_legs` refuses the whole list. So the entries are:
  * limited to the decision rows with ms in [--clock-ms, --boundary-ms) when those are given;
  * stripped of every order_id that has an `order_refused` row (no position was opened);
  * intersected with the covered ids: `--covered-ids FILE` (a JSON list of position_ids) or, from the ledger, `--ledger-root/--registry/--ledger-bot [--now-ms]`
    (arena_report_legs.covered_trades; only the ids are kept). The intersection also drops scorer-excluded trades (late print over a gap, missing V).
Without a covered set the tool says so in the summary (`covered_set: "not applied"`); such a list may be refused by the read. Only the mints of the remaining
positions are fetched.

Decision-log fields USED: kind, order_id, pool, ms and a mint from meta.mint / meta.child_mint / card.mint. The files are read once, the hash chain is verified
over exactly those bytes (a broken chain is refused) and the rows are taken from that snapshot. Chain verification PARSES every row, which for HV4 includes card
prices and reserves and sell estimates; they are parsed for chain verification only, never used or written, and the outputs depend on mints and ids only.
`--bot` is the decision-log directory name (HV4 arm `CON-L`); `hv4-CON-L` (the ledger bot_id) is accepted and mapped to `CON-L`. `--ledger-bot` is the ledger bot_id.

Modes
  from-r4b  offline: --mint-accounts mint_accounts.json --decisions-root R --bot BOT --out-dir D [covered-set options]
  fetch     network: same but fetches the traded mints; --env-file is REQUIRED (no default key-file path). Per mint: 1 try + up to 3 retries in total, retries only
            after a transport error (an RPC error reply, account not found, not a mint, bad shape deny at once), every retry within 24 h of the first try.
Outputs in --out-dir: close_blocked.json (sorted JSON list of position_ids; feeds --close-blocked unmodified), close_blocked_summary.json (counts, reasons, credits,
unresolved, dropped) and, for fetch, mint_accounts.json.

Live pre-filter: `LivePrefilter(records=..., fetcher=..., ttl_s=...).check(mint)` returns a `Verdict`; a cached or preloaded mint costs no call. A cache miss with the
stock `MF.Fetcher` makes 1-6 getAccountInfo calls (3 attempts per encoding, 1 s + 2 s backoff, 0.1 s pacing, base64 fallback), so up to about 3-6 s on a degraded RPC.
S1 should pass `OneShotFetcher` (one attempt per call, no backoff) so a miss costs at most 2 calls and no sleep.
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
from tools import arena_report_legs as RL
from tools.arena_declog import GENESIS, sha256_hex
from tools.arena_paper import canon

SPL_TOKEN_PROGRAM = MF.SPL_TOKEN_PROGRAM
TOKEN_2022_PROGRAM = MF.TOKEN_2022_PROGRAM
EXT_ALLOWLIST = frozenset({"metadataPointer", "tokenMetadata"})
# same list as tools/arena_report_legs.py FORBIDDEN_PARTS (copied, as that tool copies arena_feed_delay's)
FORBIDDEN_PARTS = ("sealed", "forward", "shadow", "canary", "probe", "walk", "exp011", "exp012", "tip-tape", "meta-capture", "fresh-08", "truth-", "_shakedown")
MAX_TRIES = 4  # Am.2 item 6: the first try plus up to 3 retries
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
    fa = rec.get("freeze_authority", False)  # the key must be present: None (no authority) or a string
    if fa is not None and not isinstance(fa, str):
        return "malformed_record"
    if fa:
        return "freeze_authority"
    ext = rec.get("token2022_extensions")
    if not isinstance(ext, list) or not all(isinstance(x, str) for x in ext):
        return "malformed_record"
    owner = rec.get("owner_program")
    if owner == SPL_TOKEN_PROGRAM:
        return None
    if owner == TOKEN_2022_PROGRAM:
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


def resolve_log_name(root: Path, bot_id: str) -> str:
    """The decision-log directory of a bot: `bot_id` itself, else `bot_id` without the `hv4-` ledger prefix (HV4 logs are named by arm, `CON-L`)."""
    for name in (bot_id, bot_id[4:] if bot_id.startswith("hv4-") else None):
        if name and (root / "decisions" / name).is_dir() and any((root / "decisions" / name).glob("*.jsonl")):
            return name
    raise Refused(f"no decision log for bot {bot_id!r} under {root}")


def snapshot_rows(root: Path, name: str) -> list[dict[str, Any]]:
    """Read every day file ONCE, verify the hash chain over exactly those bytes (same rule as DecisionLog.verify), return the parsed rows of that snapshot."""
    prev, seq = GENESIS, -1
    rows: list[dict[str, Any]] = []
    for f in sorted((root / "decisions" / name).glob("*.jsonl")):
        data = f.read_bytes()
        if data and not data.endswith(b"\n"):
            raise Refused(f"decision-log chain broken: {name}: torn last line in {f.name}")
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                sha = row.pop("sha")
            except (ValueError, KeyError, AttributeError):
                raise Refused(f"decision-log chain broken: {name}: unreadable row in {f.name}") from None
            if row.get("prev") != prev or row.get("seq") != seq + 1 or sha256_hex(prev + canon(row)) != sha:
                raise Refused(f"decision-log chain broken: {name}: chain broken at seq {seq + 1} in {f.name}")
            prev, seq = sha, row["seq"]
            rows.append(row)
    return rows


def positions_from_declog(root: str | os.PathLike, bot_id: str, clock_ms: int | None = None, boundary_ms: int | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """([{position_id, pool, mint|None, ms}], counts of what was dropped) from the `entry` rows of one bot's decision log, in [clock_ms, boundary_ms) when given,
    without any order that has an `order_refused` row. The chain is verified over the same snapshot the rows come from."""
    check_text(bot_id, "bot_id")
    root = check_path(root, "decisions root")
    rows = snapshot_rows(root, resolve_log_name(root, bot_id))
    refused = {r.get("order_id") for r in rows if r.get("kind") == "order_refused"}
    out: dict[str, dict[str, Any]] = {}
    dropped = {"refused": 0, "before_clock": 0, "at_or_after_boundary": 0}
    for row in rows:
        if row.get("kind") != "entry" or not isinstance(row.get("order_id"), str) or row["order_id"] in out:
            continue
        ms = row.get("ms")
        if row["order_id"] in refused:
            dropped["refused"] += 1
        elif clock_ms is not None and ms < clock_ms:
            dropped["before_clock"] += 1
        elif boundary_ms is not None and ms >= boundary_ms:
            dropped["at_or_after_boundary"] += 1
        else:
            out[row["order_id"]] = {"position_id": row["order_id"], "pool": row.get("pool"), "mint": _mint_of(row), "ms": ms}
    return list(out.values()), dropped


def covered_ids_from_ledger(ledger_bot: str, ledger_root: str | os.PathLike, registry: str | os.PathLike, now_ms: int | None = None) -> set[str]:
    """The read's covered trade set, ids only (arena_report_legs.covered_trades at the scorer's read boundary). The ledger rows are loaded by the report's own code;
    only position_ids leave this function."""
    check_text(ledger_bot, "ledger bot_id")
    check_path(ledger_root, "ledger root")
    check_path(registry, "registry")
    try:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        reg = RL.load_registry(registry)
        led = RL.Ledger(ledger_root)
        rows, problems = led.scan_bot(ledger_bot)
        incidents, inc_problems = led.read_incidents(ledger_bot)
        scored = RL.score_bot(ledger_bot, reg, rows, now, None, RL.SETTLE_MS, None, incidents=incidents, ledger_problems=problems + inc_problems)
        if "books" not in scored:
            raise Refused("the scorer has not reached the read boundary: no covered set")
        by_stake = RL.covered_trades(ledger_bot, reg, rows, scored["read_boundary_ms"])
    except RL.Refused as e:
        raise Refused(str(e)) from None
    return {r["position_id"] for ts in by_stake.values() for r in ts}


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


def select_positions(positions: Iterable[Mapping[str, Any]], covered: set[str] | None) -> tuple[list[dict[str, Any]], int]:
    """Intersect with the covered ids (None: not applied). Returns (kept, number dropped as not covered)."""
    pos = [dict(p) for p in positions]
    if covered is None:
        return pos, 0
    kept = [p for p in pos if p["position_id"] in covered]
    return kept, len(pos) - len(kept)


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
class TransportError(MF.FetchError):
    """A failure of the HTTP call itself (timeout, connection, HTTP status). Only this class is retried."""


class OneShotFetcher(MF.Fetcher):
    """mint_account_fetch.Fetcher with ONE attempt per call and no backoff (pacing stays). A raise from `post` is a TransportError; an RPC error reply or a
    malformed reply is a plain FetchError (not retried by the caller). mint_account_fetch.py itself is unchanged."""

    def call(self, mint: str, encoding: str) -> dict:
        self._pace()
        self.calls += 1
        payload = {"jsonrpc": "2.0", "id": self.calls, "method": "getAccountInfo", "params": [mint, {"encoding": encoding, "commitment": "finalized"}]}
        try:
            resp = self.post(self.url, payload)
        except Exception as e:  # noqa: BLE001
            raise TransportError(MF.redact(f"{type(e).__name__}: {e}", self.secrets)) from None
        if isinstance(resp, dict) and resp.get("error"):
            raise MF.FetchError("rpc error " + str((resp["error"] or {}).get("code")))
        if not isinstance(resp, dict) or "result" not in resp:
            raise MF.FetchError("malformed rpc response")
        return resp["result"]


def is_transport_failure(rec: Mapping[str, Any]) -> bool:
    return rec.get("fetch_ok") is not True and str(rec.get("error") or "").startswith("TransportError")


def fetch_records(fetcher: OneShotFetcher, mints: list[str], max_tries: int = MAX_TRIES, window_s: float = RETRY_WINDOW_S, gap_s: float = 3600.0,
                  sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, dict], dict[str, Any]]:
    """Per mint: the first try plus up to 3 retries (`max_tries` = 4 in total), same rule for every mint. A retry happens only after a TransportError and only if it
    starts within `window_s` of the first try. A try is one mint_account_fetch.fetch_one (jsonParsed, then the base64 fallback, one attempt each). Returns
    ({mint: full record}, {tries: {mint: n}, rounds_used, calls, credits_approx, still_failed})."""
    t0 = clock()
    recs: dict[str, dict] = {}
    tries: dict[str, int] = {m: 0 for m in mints}
    todo = list(mints)
    rounds = 0
    for r in range(max_tries):
        if not todo:
            break
        if r > 0:
            if clock() - t0 + gap_s > window_s:
                break
            sleep(gap_s)
        rounds += 1
        for m in todo:
            tries[m] += 1
            recs[m] = MF.fetch_one(fetcher, m)
        todo = [m for m in todo if is_transport_failure(recs[m])]
    return recs, {"tries": tries, "rounds_used": rounds, "calls": fetcher.calls, "credits_approx": fetcher.calls,
                  "still_failed": sum(1 for r in recs.values() if not r["fetch_ok"])}


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
            return Verdict(str(mint), True, "unresolved_mint", "none")
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
def build(positions: Iterable[Mapping[str, Any]], records: Mapping[str, Any], bot_id: str = "") -> tuple[list[str], dict[str, Any]]:
    positions = list(positions)
    ids, summ = close_blocked_ids(positions, records)
    mints = {p.get("mint") for p in positions if p.get("mint")}
    summ.update({"bot_id": bot_id, "traded_mints": len(mints), "mints_without_record": sum(1 for m in mints if m not in records), "outcome_blind": True})
    return ids, summ


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="traded mints -> --close-blocked position_id list (DEC-031 Am.2 item 6)")
    ap.add_argument("mode", choices=["from-r4b", "fetch"])
    ap.add_argument("--bot", required=True, help="decision-log name (HV4 arm CON-L; hv4-CON-L is accepted)")
    ap.add_argument("--decisions-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--mint-accounts", help="from-r4b: the mint_accounts.json (r4b schema)")
    ap.add_argument("--pool-mints", help="optional JSON {pool: mint} for entry rows that carry no mint")
    ap.add_argument("--clock-ms", type=int, help="drop entries with ms < this")
    ap.add_argument("--boundary-ms", type=int, help="drop entries with ms >= this (the read boundary)")
    ap.add_argument("--covered-ids", help="JSON list of the read's covered position_ids (intersect)")
    ap.add_argument("--ledger-root", help="alternative to --covered-ids: take the covered ids from this ledger via arena_report_legs")
    ap.add_argument("--registry")
    ap.add_argument("--ledger-bot", help="ledger bot_id (default: --bot)")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--env-file", type=Path, help="fetch: the Helius env file (required; no default)")
    ap.add_argument("--retry-gap-s", type=float, default=3600.0)
    a = ap.parse_args(argv)
    try:
        check_text(a.bot, "bot_id")
        out = check_path(a.out_dir, "out dir")
        pm = json.loads(check_path(a.pool_mints, "pool-mints file").read_text()) if a.pool_mints else None
        covered: set[str] | None = None
        if a.covered_ids and a.ledger_root:
            raise Refused("give --covered-ids or --ledger-root, not both")
        if a.covered_ids:
            covered = set(RL.load_json_ids(str(check_path(a.covered_ids, "covered-ids file"))))
        elif a.ledger_root:
            if not a.registry:
                raise Refused("--ledger-root needs --registry")
            covered = covered_ids_from_ledger(a.ledger_bot or a.bot, a.ledger_root, a.registry, a.now_ms)
        positions, dropped = positions_from_declog(a.decisions_root, a.bot, a.clock_ms, a.boundary_ms)
        _, positions = traded_mints(positions, pm)
        positions, dropped["not_covered"] = select_positions(positions, covered)
        mints = sorted({p["mint"] for p in positions if p.get("mint")})
        extra: dict[str, Any] = {}
        if a.mode == "from-r4b":
            if not a.mint_accounts:
                raise Refused("from-r4b needs --mint-accounts")
            records = load_r4b(a.mint_accounts)
        else:
            if a.env_file is None:
                raise Refused("fetch mode needs --env-file (no default key-file path)")
            check_path(a.env_file, "env file")
            try:
                key = MF.load_key(env_file=a.env_file)
            except MF.FetchError as e:
                print("error:", e, file=sys.stderr)
                return 2
            full, extra = fetch_records(OneShotFetcher(MF.rpc_url(key), secrets=[key]), mints, gap_s=a.retry_gap_s)
            records = to_r4b(full)["mints"]
            out.mkdir(parents=True, exist_ok=True)
            (out / "mint_accounts.json").write_text(json.dumps(to_r4b(full), indent=1, sort_keys=True) + "\n")
        ids, summ = build(positions, records, a.bot)
        summ.update(extra)
        summ["dropped_entries"] = dropped
        summ["covered_set"] = "not applied" if covered is None else {"ids": len(covered)}
        summ["window_ms"] = {"clock": a.clock_ms, "boundary": a.boundary_ms}
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
