"""r4bt_c0_tieout: the daily wallet tie-out of the dedicated r4bt-c0 wallet (DEC-032 section 6; QP-RULING-1106 build item 6). PUBLIC KEY ONLY.

    python -m tools.r4bt_c0_tieout --wallet PUBKEY --day YYYY-MM-DD --state-dir DIR --fills FILLS.jsonl --order-ledger r4bt-order-ledger.jsonl
        --attempts r4bt-attempts.jsonl --named NAMED.json --out-dir DIR [--rpc-env VAR | --rpc URL] [--first-baseline LAMPORTS --first-after-slot SLOT] [--rps 2]

It wraps the existing pieces and adds no new accounting (pattern of tools/hv4_c0_tieout.py, which exports the executor's fill rows as a ledger):
  1. fills -> LEDGER_IN {sends, trips, open_mints} with `tools.arena_tieout_ledger.export` (the executor's fill rows with a signature are the ledger sends);
  2. `tools.arena_tieout_fetch.run`: finalized public reads of the wallet's txs since the previous signed-off context slot, getBalance, token accounts (Token and Token-2022);
  3. `tools.arena_tieout.reconcile`, unchanged: balance_now - baseline - sum(per-tx deltas) == 0 to the lamport, the balance chain, every ledger send signature on chain
     and every chain signature in the ledger (sends the ledger calls dropped or expired need a confirmed drop), deposits and withdrawals only from the NAMED addresses
     (--named {deposit_from: [...], withdraw_to: [...]}, owner and Helm only; an unnamed transfer is a hard halt, a dust inflow <= 10,000 lamports is listed), buys, sells,
     fees, ATA rent, closed-trip ledger P&L against the chain sum (difference above one base fee is a halt), stranded positions (a non-zero token account whose mint is not
     an open ledger position) and open mints without a token account;
  4. three cross-checks of this wrapper: (a) every order of the order ledger (`r4bt-order-ledger.jsonl`) has a buy fill row, and every landed buy fill row belongs to an
     order; (b) the attempts file (`r4bt-attempts.jsonl`) has exactly one landed row per landed buy fill ROW (a mint re-entered after its sell counts twice), and each landed attempt's trip_id is an order; (c) a
     straddling trip (one leg before the window, one inside) is listed and refuses sign-off (the day is reviewed by hand, never netted away).

THE WINDOW is slot-based, from the previous signed-off tie-out (`TIEOUT-STATE.json` in --state-dir: balance and context_slot) to now; --day is a label. The first run of a
fresh wallet uses --first-baseline 0 --first-after-slot 0, or the owner's recorded funding balance and slot. All fill rows in the file are used (not cut to the day), because the
fetch window runs to now; a send written ahead but not yet resolved is left out by the exporter, so run the job when no order is in flight if it can be arranged
(the schedule in scripts/ops/r4bt-c0-tieout-job.sh), and a rerun after the order resolved is the fix for a false halt.

OUTPUT in --out-dir (create-exclusive, never overwritten; after a FAIL move BOTH `r4bt-tieout-<day>.json` and `fetch-<day>/` aside before a rerun): `r4bt-tieout-<day>.json` (the reconcile result plus the wrapper's counts and `verdict`). The last stdout line is
`r4bt-c0-tieout <day> PASS|FAIL` with counts only. A PASS rewrites TIEOUT-STATE.json (atomic) with the new baseline; a FAIL never does and writes `STOP-tieout` in --state-dir
only if --stop-on-fail is given (an alert marker only: nothing in the executor reads it; the 8P watchdog entry is to). The RPC URL comes from the environment variable named by --rpc-env (never printed; the Helius env file is sourced by the job script, never by
this tool) or a plain public URL. Nothing here signs or sends, and nothing says a book made money: it is an execution report, an unpromoted execution measurement.
Exit codes: 0 PASS, 1 FAIL (hard halt), 2 refused input, 3 fetch failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools import arena_tieout as T
from tools import arena_tieout_fetch as F
from tools import arena_tieout_ledger as L

SCHEMA = "r4bt_c0_tieout_v1"
STATE_NAME = "TIEOUT-STATE.json"
STATE_SCHEMA = "r4bt_c0_tieout_state_v1"
LABEL = "unpromoted execution measurement"


class Refused(Exception):
    """Unusable input (exit 2)."""


def _isint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    try:
        with open(path, "rb") as fh:
            for n, raw in enumerate(fh, 1):
                if not raw.strip():
                    continue
                try:
                    r = json.loads(raw)
                except ValueError:
                    raise Refused(f"{Path(path).name} line {n} is not JSON") from None
                if not isinstance(r, dict):
                    raise Refused(f"{Path(path).name} line {n} is not an object")
                rows.append(r)
    except OSError as exc:
        raise Refused(f"{Path(path).name} cannot be read: {type(exc).__name__}") from None
    return rows


def ledger_in(fills: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The executor's buy / sell fill rows (a row with `landed` is a resolved send) as the tie-out ledger. A resolved row precedes no `arena_send` row in the file, so one is
    made per distinct signature. Trips are paired by ORDER, not by mint (Am.9 A7 allows a re-entry after the sell lands): the exporter keys by mint, so each landed buy gets the
    trip key `<mint>#<n>` (n = its 1-based landed-buy index on that mint) and a landed sell closes the latest still-open key of its mint. The open mints returned are the real
    mints of the positions still open (a re-entered open position stays open; the stranded check compares real mints)."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    n_buys: dict[str, int] = {}
    cur: dict[str, str] = {}  # mint -> key of its open (landed buy, no landed sell yet) trip
    open_keys: dict[str, str] = {}  # key -> real mint
    for r in fills:
        if r.get("kind") not in ("buy", "sell") or "landed" not in r:
            continue
        mint, landed = r.get("mint"), r.get("landed") is True
        key = mint
        if r["kind"] == "buy" and landed:
            n_buys[mint] = n_buys.get(mint, 0) + 1
            key = f"{mint}#{n_buys[mint]}"
            if _isint(r.get("tokens_received")) and r["tokens_received"] > 0:
                cur[mint] = key
                open_keys[key] = mint
        elif r["kind"] == "sell" and mint in cur:
            key = cur[mint]
            if landed:
                open_keys.pop(key, None)
                del cur[mint]
        sig = r.get("signature")
        if isinstance(sig, str) and sig and sig not in seen:
            seen.add(sig)
            rows.append({"kind": "arena_send", "mode": "live", "mint": key, "signature": sig, "side": r["kind"],
                         "last_valid_block_height": r.get("last_valid_block_height", r.get("lvbh"))})
        rows.append({**r, "mint": key, "mode": "live"})
    out = L.export(rows, {"open": {k: {} for k in sorted(open_keys)}})
    out["open_mints"] = sorted(set(open_keys.values()))
    return out


def cross_checks(fills: Sequence[Mapping[str, Any]], orders: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The wrapper's own problems (list of {kind, ...}); empty = clean. Counts only, no P&L."""
    out: list[dict[str, Any]] = []
    order_ids = {o.get("order_id") for o in orders}
    order_mints = {o.get("mint") for o in orders}
    buy_mints = {r.get("mint") for r in fills if r.get("kind") == "buy" and "landed" in r}
    landed_buy_rows = [r for r in fills if r.get("kind") == "buy" and r.get("landed") is True]  # rows, not mints: a re-entry is a second landed buy on one mint
    landed_buy_mints = {r.get("mint") for r in landed_buy_rows}
    for m in sorted(x for x in order_mints - buy_mints if x):
        out.append({"kind": "order_without_buy_fill", "mint": m})
    for m in sorted(x for x in landed_buy_mints - order_mints if x):
        out.append({"kind": "landed_buy_without_order", "mint": m})
    landed_attempts = [a for a in attempts if a.get("landed") is True]
    if len(landed_attempts) != len(landed_buy_rows):
        out.append({"kind": "attempts_landed_count", "attempts": len(landed_attempts), "landed_buy_fills": len(landed_buy_rows)})
    for a in landed_attempts:
        if a.get("trip_id") not in order_ids:
            out.append({"kind": "attempt_trip_not_an_order", "attempt_id": a.get("attempt_id")})
    return out


def load_state(state_dir: Path, first_baseline: int | None, first_after_slot: int | None) -> tuple[int, int, str | None]:
    """(baseline lamports, after_slot, previous day label). The previous signed-off result, or the first-run values."""
    p = state_dir / STATE_NAME
    if p.exists():
        try:
            s = json.loads(p.read_text())
        except (OSError, ValueError):
            raise Refused("TIEOUT-STATE.json cannot be read") from None
        if not isinstance(s, dict) or s.get("schema") != STATE_SCHEMA or not _isint(s.get("balance_lamports")) or not _isint(s.get("context_slot")):
            raise Refused("TIEOUT-STATE.json is malformed")
        return s["balance_lamports"], s["context_slot"], s.get("day")
    if first_baseline is None or first_after_slot is None:
        raise Refused("no TIEOUT-STATE.json: the first run needs --first-baseline and --first-after-slot")
    if first_baseline < 0 or first_after_slot < 0:
        raise Refused("first baseline / slot must be >= 0")
    return first_baseline, first_after_slot, None


def check_named(named: Any) -> dict[str, list[str]]:
    if not isinstance(named, dict) or not all(isinstance(named.get(k), list) and all(isinstance(a, str) for a in named[k]) for k in ("deposit_from", "withdraw_to")):
        raise Refused("named must be {deposit_from: [addr], withdraw_to: [addr]}")
    return {k: list(named[k]) for k in ("deposit_from", "withdraw_to")}


def evaluate(txs: list[dict[str, Any]], ledger: dict[str, Any], tokens: dict[str, Any], balance: dict[str, Any], baseline: int, named: Mapping[str, Sequence[str]],
             fills: Sequence[Mapping[str, Any]], orders: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
             straddling: Sequence[Any] = ()) -> dict[str, Any]:
    """Pure: the tie-out result with the wrapper's cross-checks and `verdict` PASS|FAIL."""
    res = T.reconcile(txs, ledger, tokens, baseline, balance, named)
    extra = cross_checks(fills, orders, attempts)
    if straddling:
        extra.append({"kind": "straddling_trips", "trips": list(straddling)})
    problems = [*res["problems"], *extra]
    named_set = set(named.get("deposit_from", [])) | set(named.get("withdraw_to", []))
    res.update({"schema": SCHEMA, "label": LABEL, "wrapper_problems": extra, "problems": problems, "hard_halt": bool(problems), "signed_off": not problems,
                "next_baseline": res["balance_now"] if not problems else None, "verdict": "PASS" if not problems else "FAIL",
                "counts": {"txs": res["txs"], "ledger_sends": len(ledger["sends"]), "orders": len(orders), "attempts": len(attempts), "fill_rows": len(fills),
                           "named_addresses": len(named_set), "open_mints": len(ledger["open_mints"]), "stranded": len(res["stranded"]),
                           "dust_inflows": len(res["dust_inflows"]), "problems": len(problems)}})
    return res


def write_new(path: Path, obj: Any) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(obj, sort_keys=True, indent=1) + "\n")


def write_state(state_dir: Path, day: str, res: Mapping[str, Any]) -> None:
    p = state_dir / STATE_NAME
    tmp = state_dir / (STATE_NAME + ".tmp")
    tmp.write_text(json.dumps({"schema": STATE_SCHEMA, "day": day, "balance_lamports": res["balance_now"], "context_slot": res["balance_context_slot"]}, sort_keys=True) + "\n")
    os.replace(tmp, p)


def run(rpc: Callable[[str, list], Any], wallet: str, day: str, state_dir: Path, out_dir: Path, fills: list[dict[str, Any]], orders: list[dict[str, Any]],
        attempts: list[dict[str, Any]], named: Mapping[str, Sequence[str]], first_baseline: int | None = None, first_after_slot: int | None = None,
        stop_on_fail: bool = False) -> dict[str, Any]:
    baseline, after_slot, _prev = load_state(state_dir, first_baseline, first_after_slot)
    res_path = out_dir / f"r4bt-tieout-{day}.json"
    if res_path.exists():
        raise Refused(f"{res_path.name} already exists: a day's result is never overwritten (move the old one aside on purpose)")
    ledger = ledger_in(fills)
    fetch_dir = out_dir / f"fetch-{day}"
    manifest = F.run(rpc, wallet, day, ledger, fetch_dir, after_slot)
    rd = lambda n: json.loads((fetch_dir / n).read_text())  # noqa: E731
    res = evaluate(rd("txs.json"), rd("ledger.json"), rd("tokens.json"), rd("balance.json"), baseline, named, fills, orders, attempts, manifest.get("straddling_trips") or [])
    res.update({"day": day, "wallet": wallet, "window_after_slot": after_slot, "fetch_manifest_sha256": F.sha256_file(fetch_dir / "manifest.json")})
    write_new(res_path, res)
    if res["verdict"] == "PASS":
        write_state(state_dir, day, res)
    elif stop_on_fail:
        try:
            fd = os.open(str(state_dir / "STOP-tieout"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            os.close(fd)
        except FileExistsError:
            pass
    return res


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("--wallet", required=True, help="the dedicated r4bt-c0 wallet, a public address")
    ap.add_argument("--day", required=True, help="UTC day label YYYY-MM-DD")
    ap.add_argument("--state-dir", required=True, type=Path)
    ap.add_argument("--fills", required=True, type=Path)
    ap.add_argument("--order-ledger", required=True, type=Path)
    ap.add_argument("--attempts", required=True, type=Path)
    ap.add_argument("--named", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--first-baseline", type=int)
    ap.add_argument("--first-after-slot", type=int)
    ap.add_argument("--stop-on-fail", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--rpc", default=F.PUBLIC_RPC)
    g.add_argument("--rpc-env", metavar="VAR")
    ap.add_argument("--rps", type=float, default=2.0)
    a = ap.parse_args(argv)
    if a.rpc_env:
        url = os.environ.get(a.rpc_env, "")
        if not url:
            print(f"environment variable {a.rpc_env} is empty", file=sys.stderr)
            return 2
    else:
        url = a.rpc
        if "?" in url or "@" in url or "api-key" in url.lower():
            print("--rpc must be a plain public URL; use --rpc-env for a keyed one", file=sys.stderr)
            return 2
    if not 0 < a.rps <= 10:
        print("--rps must be in (0, 10]", file=sys.stderr)
        return 2
    try:
        named = check_named(json.loads(a.named.read_text()))
        a.out_dir.mkdir(parents=True, exist_ok=True)
        res = run(F.Rpc(url, a.rps), a.wallet, a.day, a.state_dir, a.out_dir, read_jsonl(a.fills), read_jsonl(a.order_ledger), read_jsonl(a.attempts), named,
                  a.first_baseline, a.first_after_slot, a.stop_on_fail)
    except (Refused, L.BadLedger, T.BadInput, OSError, ValueError, KeyError) as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except F.FetchError as exc:
        print(f"FETCH FAILED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({"counts": res["counts"], "residual": res["residual"], "problem_kinds": sorted({p["kind"] for p in res["problems"]})}, sort_keys=True))
    print(f"r4bt-c0-tieout {a.day} {res['verdict']}")
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
