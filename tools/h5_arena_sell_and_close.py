"""H5 ARENA canary sell-and-close (DEC-032 s5 halt 4 "Helm runs the root sell-and-close tool"): root-run rescue for ONE open position of the arena
canary executor that the executor could not sell (stuck / abandoned / strand). PAPER CODE until a canary is live; nothing here was run on a host.

What it does. Sells the FULL token balance of ONE mint that is an OPEN position in the arena executor's live state, and closes the base token account
(and the wrapped-SOL account) in the SAME transaction, so the ATA rent comes back. The transaction is built by the PINNED sell builder, the unmodified
`tools.h5_executor.H5Executor._arm_sell` (tools/h5_arena_executor.py SELL_BUILDER_PIN; the one tools/arena_sell_simulate.py (#821) drives through
simulateTransaction on Token and Token-2022), driven here exactly as #821 drives it, but with the canary key, the real balance and a real V-priced pool
snapshot. min_out is the executor's own ladder: level 0 = 0.85 of the quote, `--last-level` = 0.65, `--emergency` = 1 lamport (never 0).

The book's rule decides WHEN (fail closed, from the position's own exit plan in the state file, never from a flag):
  - a guarded sell (level 0 or the last level) only at or after the plan's `late_wall_ms` (s0 + 335 s: the book's exit was due and is late);
  - `--emergency` only at or after the plan's `deadline_wall_ms` (s0 + 400 s: the executor's own emergency instant).
A v2 position (early exit) has the same late and deadline instants, so the same bounds hold.

Refusals (each one exits 1, nothing signed): not root; a unit that may hold the key is not stopped (mal-h5-arena-executor, mal-h5-executor,
mal-probe-executor; any state but inactive/failed counts as running); unknown book; bad mint; the sell-builder pin differs from the code on disk; the
state file missing, a symlink, unreadable, or the mint not an OPEN position in it; a plan without integer late/deadline instants; before the book's
instant; the key file not root:root 0400 in a root:root 0700 dir; the key's base ATA is not the position's base ATA (another wallet); the pool is not the
mint's canonical pool; no token balance; no V-priced quote for a guarded sell; the pre-signing allowlist (validate_message) refuses; the simulate
(sigVerify true) returns an error. Nothing is sent without --send.

Run it from the pinned arena tree, with the executor STOPPED (the base `--status` / Wind-down first):
    sudo /usr/local/lib/mal-h5-arena-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-arena-exec/current/tools/h5_arena_sell_and_close.py \\
        --book-id <BOT> --mint <MINT> [--last-level | --emergency] [--send]
After a send, the position is reconciled in the executor's state by the manager (the base `--mark-closed MINT --sig SIG` reconcile, with the unit
stopped); this tool never edits the state file.
"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not __package__:  # run as a file under python -I: only the pinned root (the parent of tools/) on sys.path, never tools/ itself
    _HERE = os.path.dirname(os.path.realpath(__file__))
    _ROOT = os.path.dirname(_HERE)
    sys.path[:] = [_ROOT] + [p for p in sys.path if os.path.realpath(p or ".") != _HERE]

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import stat  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from types import MethodType, SimpleNamespace  # noqa: E402
from typing import Any, Callable  # noqa: E402

from solders.keypair import Keypair  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402

from tools import h5_arena_executor as ax  # noqa: E402
from tools import h5_executor as h  # noqa: E402
from tools import h5_sell_and_close as hs  # noqa: E402
from tools import probe_executor as pe  # noqa: E402
from tools import probe_withdraw as pw  # noqa: E402
from tools import pumpswap_tx as tx  # noqa: E402

Refuse = pw.Refuse
UNITS = ("mal-h5-arena-executor", "mal-h5-executor", "mal-probe-executor")  # none may run: two processes must never move one wallet
DEFAULT_KEYFILE = "/etc/mal-h5-arena-key/canary-wallet.json"  # the canary wallet (HELM-SHEET section F); root:root 0400 in root:root 0700
DEFAULT_RPC_ENV = pw.DEFAULT_RPC_ENV  # /etc/mal-probe-rpc/helius.env, root-only
DEFAULT_STATE_DIR = "/var/lib/mal-h5-arena"  # the arena unit's state_dir; the live state is <state_dir>/live/state-live.json
COMMITMENT = "confirmed"
LAST_LEVEL = len(h.SELL_LADDER_SLIP_BPS) - 1


def live_state_path(state_dir: str) -> Path:
    return pe.state_path_for(Path(state_dir) / h.LIVE, h.LIVE)  # h5_executor.build_probe_cfg: <state_dir>/<mode>/state-<mode>.json


def read_position(state_path: Path, mint: str) -> dict[str, Any]:
    """The OPEN position of `mint` in the executor's live state. Regular file, no symlink (O_NOFOLLOW); anything else refuses."""
    try:
        fd = os.open(state_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise Refuse(f"the executor's live state {state_path} cannot be opened (missing or a symlink)") from None
    try:
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise Refuse("the executor's live state is not a regular file")
            st = json.loads(fh.read())
    except (OSError, ValueError):
        raise Refuse("the executor's live state cannot be read as JSON") from None
    pos = (st.get("open") or {}).get(mint) if isinstance(st, dict) else None
    if not isinstance(pos, dict):
        pend = (st.get("pending") or {}).get(mint) if isinstance(st, dict) else None
        raise Refuse("this mint is a PENDING record, not an open position: reconcile the buy first" if pend else
                     "this mint is not an open position of the executor's live state (an orphan ATA is out of scope here)")
    return pos


def book_rule_refusal(pos: dict[str, Any], now_ms: int, emergency: bool) -> str | None:
    """The book's own instants from the position's exit plan: guarded sells from late_wall_ms (s0 + 335 s), emergency from deadline_wall_ms (s0 + 400 s)."""
    plan = ((pos.get("h5") or {}).get("plan")) or {}
    late, dead = plan.get("late_wall_ms"), plan.get("deadline_wall_ms")
    for name, v in (("late_wall_ms", late), ("deadline_wall_ms", dead)):
        if isinstance(v, bool) or not isinstance(v, int):
            return f"the position's plan has no integer {name}: the book's exit instant is unknown"
    if emergency and now_ms < dead:
        return f"before the book's emergency instant (deadline_wall_ms {dead}, now {now_ms}): --emergency is refused until s0 + 400 s"
    if now_ms < late:
        return f"before the book's exit was late (late_wall_ms {late}, now {now_ms}): the executor sells this position itself"
    return None


def build_rescue(rpc: Callable, kp: Keypair, book: ax.Book, mint: str, pos: dict[str, Any], tokens: int, level: int, emergency: bool, now_ms: int) -> dict[str, Any]:
    """Drive the pinned H5Executor._arm_sell (as tools/arena_sell_simulate.build_sell does) with the canary key, the chain balance and a real
    V-priced snapshot. Returns the pending record (signed tx_b64, min_out, priority) or refuses."""
    user = kp.pubkey()
    snap = pe.fetch_snapshot(rpc, pos["pool"], COMMITMENT, user)
    if isinstance(snap, str):
        raise Refuse(f"the position's pool is not usable ({snap})")
    ps = snap.ps
    if str(ps.base_mint) != mint or ps.pool != tx.canonical_pool(Pubkey.from_string(mint)) or str(ps.pool) != pos["pool"]:
        raise Refuse("the position's pool is not the mint's canonical pool")
    alerts: list[tuple[str, dict]] = []
    stub = SimpleNamespace(
        user=user, _kp=kp, dry_run=False, armed={}, pool_cache={mint: (ps,)}, exit_commitment=COMMITMENT, exit_poll_ms=0,
        h5=SimpleNamespace(stake_lamports=int(pos.get("spend") or ax.C0_TIER["stake_lamports"]), sell_priority_lamports=book.priority.sell,
                           escalated_priority_lamports=book.priority.escalated, sell_cu_limit=tx.DEFAULT_SELL_CU_LIMIT))
    stub.now_ms = lambda: now_ms
    stub._sell_balance = lambda p: (tokens, "rescue_chain_balance")
    stub._snapshot = lambda m, c=None: (snap, pos["pool"], "")
    stub._alert = lambda kind, mint_=None, **kw: alerts.append((kind, kw))
    stub._due = lambda *a, **kw: False  # never the blind floor: a guarded rescue needs a quote
    stub._exit_fields = lambda kind: {"exit_poll_ms": 0, "exit_commitment": COMMITMENT, "exit_snapshot": kind}
    stub.bh = pl_blockhash(rpc)
    stub._signer_spend_cap = MethodType(h.H5Executor._signer_spend_cap, stub)
    stub._sign = MethodType(h.H5Executor._sign, stub)  # validate_message (the pre-signing allowlist) runs inside, then the signature
    work = {**pos, "sell_attempts": 0, "sell_sigs": []}
    rec = h.H5Executor._arm_sell(stub, mint, work, level, emergency)  # type: ignore[arg-type]
    if rec is None:
        why = ",".join(k for k, _ in alerts) or "no_record"
        raise Refuse(f"the pinned builder built no sell ({why}): a guarded sell needs a V-priced quote; nothing was signed")
    return rec


def pl_blockhash(rpc: Callable) -> Any:
    from tools import probe_live as pl
    cache = pl.BlockhashCache(rpc, COMMITMENT, lambda: int(time.time() * 1000))
    return SimpleNamespace(get=lambda fresh=False: cache.get(fresh=True))


def simulate(rpc: Callable, tx_b64: str) -> dict[str, Any]:
    res = rpc("simulateTransaction", [tx_b64, {"encoding": "base64", "sigVerify": True, "commitment": COMMITMENT}])["value"]
    if res.get("err"):
        raise Refuse(f"simulation failed: {res['err']}; nothing was sent")
    return res


def send_and_confirm(rpc: Callable, tx_b64: str, sleep: Callable[[float], None] = time.sleep, timeout_s: float = 60.0) -> str:
    sig = rpc("sendTransaction", [tx_b64, {"encoding": "base64", "skipPreflight": False}])
    waited = 0.0
    while True:
        st = rpc("getSignatureStatuses", [[sig]])["value"][0]
        if st is not None:
            if st.get("err"):
                raise Refuse(f"transaction {sig} failed: {st['err']}")
            if st.get("confirmationStatus") in ("confirmed", "finalized"):
                return sig
        if waited >= timeout_s:
            raise Refuse(f"timeout confirming {sig}; check the explorer before retrying")
        sleep(1.0)
        waited += 1.0


def run(args: argparse.Namespace, rpc: Callable, *, is_active: Callable[[str], bool] = hs.unit_running, out: Callable[[str], None] = print,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000), sleep: Callable[[float], None] = time.sleep, check_location: bool = True,
        sell_pin: Callable[[], str | None] = ax.sell_pin_problem) -> int:
    book = ax.BOOKS.get(args.book_id)
    if book is None:
        raise Refuse(f"--book-id must be one of {sorted(ax.BOOKS)}")
    mint_pk = hs.parse_mint(args.mint)
    mint = str(mint_pk)
    if args.emergency and args.last_level:
        raise Refuse("--emergency and --last-level are exclusive")
    level = LAST_LEVEL if args.last_level else 0
    for unit in UNITS:
        if is_active(unit):
            raise Refuse(f"refusing: {unit} is not stopped (ActiveState is not inactive or failed); stop it first")
    why = sell_pin()
    if why:
        raise Refuse(f"the pinned sell builder differs from the code on disk ({why}); nothing is built")
    pos = read_position(live_state_path(args.state_dir), mint)
    why = book_rule_refusal(pos, now_ms(), args.emergency)
    if why:
        raise Refuse(why)
    if check_location:
        pw.check_key_location(args.keyfile)
    kp = pw.load_keypair(args.keyfile)
    user = kp.pubkey()
    base_tp = Pubkey.from_string(pos["base_tp"])
    base_ata = tx.ata(user, mint_pk, base_tp)
    if str(base_ata) != pos.get("base_ata"):
        raise Refuse("this key's token account is not the position's base ATA (another wallet); nothing is signed")
    acct = hs.read_token_account(rpc, user, mint_pk, base_ata)
    if not acct or acct["amount"] <= 0:
        raise Refuse("no token balance for this position: nothing to sell (a close-only is not this tool's job)")
    rec = build_rescue(rpc, kp, book, mint, pos, acct["amount"], level, args.emergency, now_ms())
    wsol_lamports = hs.read_wsol_account(rpc, tx.ata(user, tx.WSOL_MINT, tx.TOKEN_PROGRAM)) or 0
    out(f"book:    {book.bot_id}   wallet: {user}   mint: {mint}   pool: {pos['pool']}")
    out(f"tokens:  {acct['amount']} (the full balance)   quote: {rec.get('q_out')}   min_out: {rec['min_out']}   level: {rec['h5']['level']}"
        f"   emergency: {rec['h5']['emergency']}   priority: {rec['h5']['priority']}")
    out(f"close:   base ATA {base_ata} ({acct['lamports']} lamports rent) under {base_tp}, in the same transaction")
    sim = simulate(rpc, rec["tx_b64"])
    out(f"simulated OK: units={sim.get('unitsConsumed')}")
    if not args.send:
        out("dry run: nothing sent. Re-run with --send to send.")
        return 0
    sig = send_and_confirm(rpc, rec["tx_b64"], sleep)
    out(f"signature: {sig}")
    fill = hs.fetch_fill(rpc, sig, {"tokens": acct["amount"], "base_ata_lamports": acct["lamports"], "wsol_lamports": wsol_lamports}, sleep)
    out("fill: unavailable (confirmed; read it from the signature)" if fill is None else "fill: " + " ".join(f"{k}={v}" for k, v in fill.items()))
    out("next: the manager reconciles the position in the executor's state (base --mark-closed with --sig) before the unit starts again")
    return 0


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("MAL_LIVE_TEST") == "1" and os.geteuid() == 0:
        print("MAL_LIVE_TEST is not allowed as root", file=sys.stderr)
        return 1
    pw.harden_process()
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--mint", required=True, help="the one open position to sell and close")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--keyfile", default=DEFAULT_KEYFILE)
    ap.add_argument("--rpc-env", default=DEFAULT_RPC_ENV)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--last-level", action="store_true", help="the executor's last ladder level: min_out = 0.65 x quote, escalated priority")
    g.add_argument("--emergency", action="store_true", help="min_out = 1 lamport; only from the book's deadline (s0 + 400 s)")
    ap.add_argument("--send", action="store_true", help="send it (the default is to simulate only)")
    args = ap.parse_args(argv)
    if os.geteuid() != 0 and os.environ.get("MAL_LIVE_TEST") != "1":
        print("refusing: the arena sell-and-close is root-only", file=sys.stderr)
        return 1
    print(f"h5_arena_sell_and_close sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}", file=sys.stderr)
    try:
        return run(args, pw.Rpc(pw.load_rpc_url(args.rpc_env)), check_location=os.environ.get("MAL_LIVE_TEST") != "1")
    except SystemExit as e:
        print(pw.redact(str(e.code)), file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(pw.redact(f"failed: {type(e).__name__}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
