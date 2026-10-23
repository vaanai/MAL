"""Fetcher for the arena daily tie-out (DEC-032 section 6): builds the input files tools/arena_tieout.py reads, from PUBLIC RPC, for a wallet ADDRESS.

    python -m tools.arena_tieout_fetch --wallet PUBKEY --day LABEL --after-slot SLOT --ledger LEDGER_IN.json --out-dir DIR [--rpc URL | --rpc-env VAR] [--rps 2]

PUBLIC KEY ONLY. No key file is opened, nothing is signed or sent, only read methods are called: getBalance, getBlockHeight, getSignaturesForAddress, getTransaction,
getTokenAccountsByOwner, getSignatureStatuses. Every read is at FINALIZED commitment. The RPC URL is never printed (use --rpc-env VAR for a keyed URL; a URL with a query
string or user-info is refused on the command line).

WRITES in --out-dir (0644; refuses to overwrite):
  txs.json      every chain tx of the wallet in slots STRICTLY AFTER --after-slot (the previous signed-off tie-out's balance.json context_slot) up to NOW, one row per tx in the shape arena_tieout reads: signature, slot, status ("ok"|"failed"),
                pre_balance / post_balance (lamports of the wallet, from meta), fee_lamports (what the WALLET paid: 0 when it is not the fee payer), priority_fee_lamports
                (meta.fee minus 5,000 per signature), kind ("transfer"|"buy"|"sell"|"other") and the kind's fields. Same-slot txs are put in balance-chain order.
                The window starts where the baseline (the previous signed-off balance) was taken, never at 00:00Z: a tx before that instant is already inside the baseline and a tx after it is not, so nothing is
                counted twice and nothing is missed after a halt-time run. It runs to now because the balance below is the balance now. --day is only a label in the manifest.
  balance.json  its OWN finalized getBalance: {lamports, context_slot, commitment, finalized_block_height} (getBlockHeight at finalized, right after).
  tokens.json   {programs_covered: [Token, Token-2022], accounts: [{mint, token_account, amount}]} from getTokenAccountsByOwner for BOTH programs, zero-amount accounts kept.
  ledger.json   the executor's ledger (--ledger, {sends, trips, open_mints}) CUT TO THE SAME WINDOW, with `status_check` {search_transaction_history: true, result: <null|status>} added to every
                kept send whose signature is NOT on chain in txs.json (getSignatureStatuses, searchTransactionHistory=true). The cut uses chain evidence, not clocks: a send or trip leg
                that is not in the window but whose finalized status slot is <= --after-slot belongs to an earlier window and is removed. A CLOSED trip with both legs earlier is removed;
                a closed trip with ONE leg earlier and one in the window (a straddler) is removed too and listed in the manifest `straddling_trips` (the tie-out cannot check its
                realized P&L: the balance chain still covers its SOL, and the list is the owner's to review). A leg with no status (unknown) is KEPT, so the tie-out fails closed on it.
                open_mints and open trips are copied unchanged.
  manifest.json counts, the request count, sha256 of each file, the first tx's pre-balance (information only: the BASELINE is the previous signed-off tie-out's, never this), and
                the list of txs classified "other".
named.json (the owner's and Helm's addresses) is not made here.

CLASSIFICATION (from the raw instructions and meta; no parsed encoding). A tx is only ever "buy" or "sell" when its one PumpSwap instruction has a known discriminator, every other
program is ComputeBudget / System / Token / Token-2022 / ATA, and (for an ok tx) the sign of the wallet's change in its base-token balance agrees with the side. sol_paid / sol_received
are NOT taken from the wallet's own delta (that would make arena_tieout's buy/sell check a tautology): they are the net change of WSOL token balances of every account NOT owned by the
wallet (pool vault + fee recipients; the pool fee is inside, as the tie-out states). rent_paid = lamports of a wallet-owned non-WSOL token account created in the tx;
rent_refunded = the lamports of one closed. A lone System transfer that touches the wallet is "transfer" (counterparty = the other side). A tx of only token-account closes is
"other" with detail "ata_close". Anything else is "other" (the tie-out halts on it: fail closed).

Nothing here says a book made money. It is an execution-measurement input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from solders.pubkey import Pubkey

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_PROGRAMS = (TOKEN_PROGRAM, TOKEN_2022_PROGRAM)
SYSTEM_PROGRAM = "11111111111111111111111111111111"
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL = "So11111111111111111111111111111111111111112"
DISC_BUY = bytes.fromhex("66063d1201daebea")
DISC_BUY_EXACT_QUOTE_IN = bytes.fromhex("c62e1552b4d9e870")
DISC_SELL = bytes.fromhex("33e685a4017f83ad")
BASE_FEE_PER_SIG = 5_000
SIG_PAGE = 1000
STATUS_BATCH = 256
ALLOWED_IN_SWAP = {COMPUTE_BUDGET, SYSTEM_PROGRAM, ATA_PROGRAM, *TOKEN_PROGRAMS}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class FetchError(Exception):
    pass


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body


class Rpc:
    """JSON-RPC, at most `rps` requests/s, 3 tries per call. `post`, `sleep`, `clock` are injectable for tests. The URL is never in an error text."""

    def __init__(self, url: str, rps: float = 2.0, post: Callable[[str, dict], dict] | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.url, self.iv, self.post, self.sleep, self.clock = url, 1.0 / rps, post or self._http, sleep, clock
        self.next = 0.0
        self.calls: dict[str, int] = {}

    @staticmethod
    def _http(url: str, payload: dict) -> dict:
        req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=40))

    def __call__(self, method: str, params: list) -> Any:
        for attempt in range(3):
            wait = self.next - self.clock()
            if wait > 0:
                self.sleep(wait)
            self.next = self.clock() + self.iv
            self.calls[method] = self.calls.get(method, 0) + 1
            try:
                resp = self.post(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            except Exception as exc:  # noqa: BLE001  never print the URL
                last = f"{type(exc).__name__}"
            else:
                if "error" not in resp:
                    return resp["result"]
                last = f"rpc error code {(resp['error'] or {}).get('code')}"
            self.sleep(1.5 * (attempt + 1))
        raise FetchError(f"{method} failed after 3 tries ({last})")


# --- window: strictly after the previous signed-off slot
def day_bounds(day: str) -> tuple[int, int]:  # kept for callers that label a day; the fetch window itself is slot-based
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp()), int((d + timedelta(days=1)).timestamp())


# --- the four finalized reads
def fetch_balance(rpc: Callable[[str, list], Any], wallet: str) -> dict[str, Any]:
    got = rpc("getBalance", [wallet, {"commitment": "finalized"}])
    height = rpc("getBlockHeight", [{"commitment": "finalized"}])
    return {"lamports": int(got["value"]), "context_slot": int(got["context"]["slot"]), "commitment": "finalized", "finalized_block_height": int(height)}


def page_signatures(rpc: Callable[[str, list], Any], wallet: str, after_slot: int) -> list[dict[str, Any]]:
    """Newest first, until a page's oldest tx is at or before after_slot. Only slots > after_slot are returned."""
    out: list[dict[str, Any]] = []
    before = None
    while True:
        cfg: dict[str, Any] = {"limit": SIG_PAGE, "commitment": "finalized"}
        if before:
            cfg["before"] = before
        page = rpc("getSignaturesForAddress", [wallet, cfg])
        if not page:
            break
        out.extend(page)
        before = page[-1]["signature"]
        if len(page) < SIG_PAGE or page[-1]["slot"] <= after_slot:
            break
    return [p for p in out if p["slot"] > after_slot]


def fetch_tokens(rpc: Callable[[str, list], Any], wallet: str) -> dict[str, Any]:
    accounts: list[dict[str, Any]] = []
    slots = {}
    for prog in TOKEN_PROGRAMS:
        got = rpc("getTokenAccountsByOwner", [wallet, {"programId": prog}, {"encoding": "jsonParsed", "commitment": "finalized"}])
        slots[prog] = got["context"]["slot"]
        for it in got["value"]:
            info = it["account"]["data"]["parsed"]["info"]
            accounts.append({"mint": info["mint"], "token_account": it["pubkey"], "amount": str(info["tokenAmount"]["amount"]), "token_program": prog})
    return {"programs_covered": list(TOKEN_PROGRAMS), "accounts": accounts, "context_slots": slots}


def lookup_statuses(rpc: Callable[[str, list], Any], sigs: list[str]) -> dict[str, Any]:
    """getSignatureStatuses with searchTransactionHistory=true, in batches. Returns signature -> status object or None."""
    out: dict[str, Any] = {}
    uniq = list(dict.fromkeys(sigs))
    for i in range(0, len(uniq), STATUS_BATCH):
        chunk = uniq[i:i + STATUS_BATCH]
        got = rpc("getSignatureStatuses", [chunk, {"searchTransactionHistory": True}])["value"]
        if len(got) != len(chunk):
            raise FetchError("getSignatureStatuses returned a different number of results")
        out.update(zip(chunk, got))
    return out


def cut_ledger(rpc: Callable[[str, list], Any], ledger: dict[str, Any], on_chain: set[str], after_slot: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """The ledger cut to the window (after_slot, now]; status_check on every kept send that is not on chain. Returns (ledger, report). See the module docstring."""
    out = json.loads(json.dumps(ledger))
    need = [s["signature"] for s in out["sends"] if s["signature"] not in on_chain]
    for t in out["trips"]:
        if t.get("closed"):
            need += [t[k] for k in ("buy_sig", "sell_sig") if t.get(k) and t[k] not in on_chain]
    status = lookup_statuses(rpc, need)

    def earlier(sig: str | None) -> bool:
        st = status.get(sig) if sig else None
        return bool(st) and st.get("slot") is not None and st["slot"] <= after_slot

    kept_sends, cut_sends = [], []
    for sd in out["sends"]:
        if sd["signature"] in on_chain:
            kept_sends.append(sd)
        elif earlier(sd["signature"]):
            cut_sends.append(sd["signature"])
        else:
            sd["status_check"] = {"search_transaction_history": True, "result": status[sd["signature"]]}
            kept_sends.append(sd)
    kept_trips, cut_trips, straddling = [], [], []
    for t in out["trips"]:
        if not t.get("closed"):
            kept_trips.append(t)
            continue
        legs = {k: ("window" if t.get(k) in on_chain else "earlier" if earlier(t.get(k)) else "unknown") for k in ("buy_sig", "sell_sig")}
        if set(legs.values()) == {"earlier"}:
            cut_trips.append(t.get("trip_id"))
        elif "earlier" in legs.values() and "unknown" not in legs.values():
            straddling.append({"trip_id": t.get("trip_id"), "legs": legs, "realized_lamports": t.get("realized_lamports")})
        else:
            kept_trips.append(t)  # both legs in the window, or a leg with no status: keep, the tie-out fails closed on a missing leg
    out["sends"], out["trips"] = kept_sends, kept_trips
    return out, {"cut_sends": len(cut_sends), "cut_trips": cut_trips, "straddling_trips": straddling,
                 "sends_checked_not_on_chain": sum(1 for sd in kept_sends if "status_check" in sd)}


# --- classification
def _keys(tx: dict[str, Any]) -> list[str]:
    msg, meta = tx["transaction"]["message"], tx.get("meta") or {}
    la = meta.get("loadedAddresses") or {}
    return [*msg["accountKeys"], *(la.get("writable") or []), *(la.get("readonly") or [])]


def _ixs(tx: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    return [{"program": keys[i["programIdIndex"]], "data": b58decode(i["data"]) if i.get("data") else b"", "accounts": [keys[a] for a in i["accounts"]]}
            for i in tx["transaction"]["message"]["instructions"]]


def _tok_map(meta: dict[str, Any], which: str) -> dict[int, dict[str, Any]]:
    return {int(b["accountIndex"]): b for b in (meta.get(which) or [])}


def classify_tx(tx: dict[str, Any], wallet: str) -> dict[str, Any]:
    """One getTransaction result (encoding "json", maxSupportedTransactionVersion 0) -> a tie-out row."""
    meta, msg = tx["meta"], tx["transaction"]["message"]
    keys = _keys(tx)
    if wallet not in keys:
        raise FetchError("wallet is not in the tx account keys")
    w = keys.index(wallet)
    nsig = int(msg["header"]["numRequiredSignatures"])
    payer = keys[0] == wallet
    fee_total = int(meta["fee"])
    fee = BASE_FEE_PER_SIG * nsig if payer else 0
    prio = max(0, fee_total - BASE_FEE_PER_SIG * nsig) if payer else 0
    row: dict[str, Any] = {"signature": tx["transaction"]["signatures"][0], "slot": int(tx["slot"]), "status": "failed" if meta.get("err") is not None else "ok",
                           "pre_balance": int(meta["preBalances"][w]), "post_balance": int(meta["postBalances"][w]), "fee_lamports": fee, "priority_fee_lamports": prio,
                           "block_time": tx.get("blockTime")}
    ixs = _ixs(tx, keys)
    work = [i for i in ixs if i["program"] != COMPUTE_BUDGET]
    progs = {i["program"] for i in work}
    pre_t, post_t = _tok_map(meta, "preTokenBalances"), _tok_map(meta, "postTokenBalances")
    pre_b, post_b = meta["preBalances"], meta["postBalances"]

    def other(detail: str) -> dict[str, Any]:
        return {**row, "kind": "other", "detail": detail}

    # wallet-owned non-WSOL token accounts: created / closed / base-token change
    mine_post = {i: b for i, b in post_t.items() if b.get("owner") == wallet and b["mint"] != WSOL}
    mine_pre = {i: b for i, b in pre_t.items() if b.get("owner") == wallet and b["mint"] != WSOL}
    rent_paid = sum(post_b[i] for i in mine_post if pre_b[i] == 0 and post_b[i] > 0)
    rent_refunded = sum(pre_b[i] for i in mine_pre if pre_b[i] > 0 and post_b[i] == 0)
    base_change = sum(int(b["uiTokenAmount"]["amount"]) for b in mine_post.values()) - sum(int(b["uiTokenAmount"]["amount"]) for b in mine_pre.values())

    swaps = [i for i in work if i["program"] == PUMPSWAP]
    if swaps:
        unknown = sorted(progs - ALLOWED_IN_SWAP - {PUMPSWAP})
        if unknown:
            return other("unknown_program:" + ",".join(unknown))
        if len(swaps) != 1:
            return other("multiple_pumpswap_ixs")
        d = swaps[0]["data"][:8]
        side = "buy" if d in (DISC_BUY, DISC_BUY_EXACT_QUOTE_IN) else "sell" if d == DISC_SELL else None
        if side is None:
            return other("unknown_pumpswap_discriminator")
        if row["status"] == "failed":
            return {**row, "kind": side, "sol_paid" if side == "buy" else "sol_received": 0, "rent_paid" if side == "buy" else "rent_refunded": 0}
        if (side == "buy" and base_change <= 0) or (side == "sell" and base_change >= 0):
            return other(f"side_mismatch:{side}:base_token_change={base_change}")
        wsol_other = 0
        for i in set(pre_t) | set(post_t):
            b = post_t.get(i) or pre_t[i]
            if b["mint"] != WSOL or b.get("owner") == wallet:
                continue
            wsol_other += int((post_t.get(i) or {"uiTokenAmount": {"amount": "0"}})["uiTokenAmount"]["amount"]) - int((pre_t.get(i) or {"uiTokenAmount": {"amount": "0"}})["uiTokenAmount"]["amount"])
        if side == "buy":
            return {**row, "kind": "buy", "sol_paid": wsol_other, "rent_paid": rent_paid}
        return {**row, "kind": "sell", "sol_received": -wsol_other, "rent_refunded": rent_refunded}
    transfers = [i for i in work if i["program"] == SYSTEM_PROGRAM]
    if work and len(transfers) == len(work):
        mine = [i for i in transfers if len(i["data"]) == 12 and i["data"][:4] == b"\x02\x00\x00\x00" and wallet in i["accounts"][:2]]
        if len(mine) == 1 and len(transfers) == 1:
            src, dst = mine[0]["accounts"][0], mine[0]["accounts"][1]
            return {**row, "kind": "transfer", "counterparty": dst if src == wallet else src}
        return other("system_ixs_not_a_single_transfer_of_the_wallet")
    if work and all(i["program"] in TOKEN_PROGRAMS and i["data"][:1] == b"\x09" for i in work):
        return {**other("ata_close"), "rent_refunded": rent_refunded}
    return other("unrecognized_instructions")


def chain_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by slot; inside one slot put the txs in balance-chain order (each pre_balance = the previous post_balance) when that order is unique. Otherwise the input order stays."""
    out: list[dict[str, Any]] = []
    for slot in sorted({r["slot"] for r in rows}):
        grp = [r for r in rows if r["slot"] == slot]
        if len(grp) > 1:
            starts = [r for r in grp if not any(o is not r and o["post_balance"] == r["pre_balance"] and o["post_balance"] != o["pre_balance"] for o in grp)]
            order, left = [], list(grp)
            cur = starts[0] if len(starts) == 1 else None
            while cur is not None:
                order.append(cur)
                left.remove(cur)
                nxt = [r for r in left if r["pre_balance"] == cur["post_balance"]]
                cur = nxt[0] if len(nxt) == 1 else None
            if not left:
                grp = order
        out.extend(grp)
    return out


def fetch_txs(rpc: Callable[[str, list], Any], wallet: str, sigs: list[dict[str, Any]], after_slot: int) -> list[dict[str, Any]]:
    rows = []
    for s in sigs:
        tx = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}])
        if tx is None:
            raise FetchError(f"getTransaction returned null for {s['signature']} (not yet finalized or pruned): the day cannot be tied out")
        if tx["slot"] <= after_slot:
            continue
        rows.append(classify_tx(tx, wallet))
    return chain_order(rows)


# --- orchestration
def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_new(path: Path, obj: Any) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(obj, indent=1, sort_keys=True) + "\n")


def run(rpc: Callable[[str, list], Any], wallet: str, day: str, ledger: dict[str, Any], out_dir: Path, after_slot: int, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    Pubkey.from_string(wallet)  # a public key, nothing else
    if not isinstance(ledger, dict) or any(not isinstance(ledger.get(k), list) for k in ("sends", "trips", "open_mints")):
        raise FetchError("ledger must hold the lists sends, trips, open_mints")
    if not isinstance(after_slot, int) or isinstance(after_slot, bool) or after_slot < 0:
        raise FetchError("after_slot must be the previous signed-off context_slot (an integer >= 0)")
    out_dir.mkdir(parents=True, exist_ok=True)
    # order matters: the signature list first, then the balance last, so the balance context slot is at or after every tx listed
    sigs = page_signatures(rpc, wallet, after_slot)
    txs = fetch_txs(rpc, wallet, sigs, after_slot)
    tokens = fetch_tokens(rpc, wallet)
    balance = fetch_balance(rpc, wallet)
    ledger_out, cut = cut_ledger(rpc, ledger, {t["signature"] for t in txs}, after_slot)
    files = {"txs.json": txs, "balance.json": balance, "tokens.json": tokens, "ledger.json": ledger_out}
    for name, obj in files.items():
        write_new(out_dir / name, obj)
    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "kind": "arena_tieout_fetch.v1", "utc": stamp, "wallet": wallet, "day_label": day, "window_after_slot": after_slot, "commitment": "finalized",
        "txs": len(txs),
        "first_tx_pre_balance_information_only": txs[0]["pre_balance"] if txs else None,
        "last_tx_slot": txs[-1]["slot"] if txs else None, "balance_context_slot": balance["context_slot"],
        "balance_context_at_or_after_last_tx": (not txs) or balance["context_slot"] >= txs[-1]["slot"],
        "other_txs": [{"signature": t["signature"], "detail": t.get("detail")} for t in txs if t["kind"] == "other"],
        **cut,
        "rpc_calls": dict(getattr(rpc, "calls", {})), "files_sha256": {n: sha256_file(out_dir / n) for n in files},
    }
    write_new(out_dir / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("--wallet", required=True, help="a public address")
    ap.add_argument("--day", required=True, help="a label for the manifest (the window is --after-slot, not the day)")
    ap.add_argument("--after-slot", required=True, type=int, help="the previous signed-off tie-out's balance.json context_slot (0 only for the very first run)")
    ap.add_argument("--ledger", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--rpc", default=PUBLIC_RPC)
    g.add_argument("--rpc-env", metavar="VAR", help="name of an environment variable that holds the RPC URL (never printed)")
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
        ledger = json.loads(a.ledger.read_text())
        m = run(Rpc(url, a.rps), a.wallet, a.day, ledger, a.out_dir, a.after_slot)
    except (FetchError, OSError, ValueError, KeyError) as exc:
        print(f"FETCH FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: m[k] for k in ("day_label", "window_after_slot", "txs", "balance_context_at_or_after_last_tx", "other_txs", "sends_checked_not_on_chain", "cut_sends", "cut_trips", "straddling_trips")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
