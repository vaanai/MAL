"""r4bt_c0_sell_simulate: simulate (never send) a full-balance sell + base-ATA close of a recorded mint through the executor's sell builder
(DEC-032 FILL:simulate_ref for the r4bt-c0 book; QP-RULING-1106-r4bt-c0 build item 7P). PUBLIC KEY ONLY, NO SIGNATURE.

    python -m tools.r4bt_c0_sell_simulate --mint MINT --public-non-r4bt-mint [--rpc URL] [--out RECORD.json]

The builder is the EXP027_SELL_BUILDER one: `tools/probe_live.LiveExecutor._start_sell` (tx.sell_instructions + `probe_live.close_token_account` under the mint's own token
program, signed through `_sign` and `probe_live.validate_message`), reached the way the bridge reaches it, `arena_live_bridge.make_live_port` with the plain (no hv4_c0)
profile and `port.sell(...)`, first rung (sell_attempts 0). Nothing here re-implements it; the harness is the one of `tools/hv4_c0_sell_simulate.py` (the RPC allowlist
gate `SimRpc`: `sendTransaction` is answered locally with the captured signature and never forwarded; read methods and simulateTransaction only).

Flow: the bridge's own snapshot of the mint's canonical pool; a PUBLIC ordinary holder of the mint (`arena_sell_simulate.find_holder`); the owner is that holder's public
key behind a NullSigner (zero signature); the sell amount is the holder's whole balance; `simulateTransaction` with sigVerify=false, replaceRecentBlockhash=true.

OUTPUT (stdout, one JSON line, and --out): success (simulate err is null), the err class (`arena_sell_simulate.classify`) on a failure, compute units consumed, the token
program name, the mint, `sent` 0. Nothing about price, proceeds or P&L. A clean simulate is not a landing.

SCOPE: until the post-read run (QP-RULING-1106 item 12) the mint must be a public canonical mint that is NOT an r4-b-T mint; --public-non-r4bt-mint is the operator's
statement that it is one (the tool cannot know: the r4-b-T universe is sealed and this tool reads none of it). Exit codes: 0 success, 1 simulate failed, 2 refused, 3 error.
No book has cleared the promotion gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from solders.keypair import Keypair  # noqa: F401  (the signer type of SimCtx; no key is made here)
from solders.null_signer import NullSigner
from solders.pubkey import Pubkey

from tools import arena_live_bridge as alb
from tools import arena_sell_simulate as ss
from tools import hv4_c0_sell_simulate as hs
from tools import probe_executor as pe
from tools import pumpswap_tx as tx

KIND = "r4bt_c0_sell_simulate.v1"
SIM_BOT_ID = "r4bt-SELLSIM"
PROBE_LIVE = Path(__file__).resolve().parent / "probe_live.py"


def make_ctx(gate: hs.SimRpc, owner: Pubkey):
    """`arena_live_bridge.make_live_port` for the plain profile in a tmp dir (host guards redirected, as in hv4_c0_sell_simulate.sim_port; the transaction is unaffected)."""
    import shutil
    import tempfile
    import time
    from contextlib import contextmanager

    @contextmanager
    def cm():
        tmp = Path(tempfile.mkdtemp(prefix="r4bt-c0-sellsim-"))
        live_dir = tmp / "live-dir-empty"
        live_dir.mkdir()
        saved = pe.LIVE_DIR
        try:
            cfg = alb.Config.from_dict({"bot_id": SIM_BOT_ID, "frozen_sha": "0" * 40, "root": str(tmp), "state_dir": str(tmp / "state"),
                                        "end_ms": int(time.time() * 1000) + hs.SIM_END_MS, "max_hold_ms": hs.SIM_MAX_HOLD_MS, "mode": "dry",
                                        "stop_file": str(tmp / "STOP"), "halt_file": str(tmp / "HALT")})
            pe.LIVE_DIR = live_dir
            try:
                port = alb.make_live_port(cfg, gate, NullSigner(owner))
            finally:
                pe.LIVE_DIR = saved
            yield hs.SimCtx(port, gate, NullSigner(owner))
        finally:
            pe.LIVE_DIR = saved
            shutil.rmtree(tmp, ignore_errors=True)

    return cm()


def run(rpc: Callable[[str, list], Any], mint: str) -> dict[str, Any]:
    canon = hs.check_pool(mint, None)
    gate = hs.SimRpc(rpc)
    with make_ctx(gate, Pubkey.from_string(hs.SYSTEM)) as pre:  # the bridge's snapshot names the mint's token program for the holder search
        tp0 = hs.prime(pre, mint)[0].ps.base_token_program
    holder = ss.find_holder(gate, mint, Pubkey.from_string(canon), tp0)
    owner = Pubkey.from_string(holder["owner"])
    with make_ctx(gate, owner) as ctx:
        snap, entry, _mayhem = hs.prime(ctx, mint)
        balance, _slot = ss.read_balance(gate, holder["token_account"])
        built = hs.build_rung(ctx, mint, snap, "first", balance, entry)
        out: dict[str, Any] = {"kind": KIND, "mint": mint, "pool": canon, "token_program": ss.TOKEN_NAMES.get(str(snap.ps.base_token_program), str(snap.ps.base_token_program)),
                               "builder": "tools/probe_live.py LiveExecutor._start_sell via arena_live_bridge.make_live_port (plain profile)",
                               "probe_live_sha256": hashlib.sha256(PROBE_LIVE.read_bytes()).hexdigest(), "sent": 0}
        if not built["built"]:
            out.update({"built": False, "success": False, "err_class": "not_built", "compute_units": None})
        else:
            res = ss.simulate(gate, built["tx_b64"])
            ok = res["err"] is None
            out.update({"built": True, "success": ok, "err_class": None if ok else ss.classify({"err": res["err"], "logs": res["logs"]})["class"],
                        "compute_units": res["units_consumed"]})
    if gate.refused or (out["built"] and len(gate.captured_sends) != 1):  # the builder asked for a method outside the allowlist, or sent other than once (captured, never forwarded)
        out["success"] = False
        out["err_class"] = "gate_refused" if gate.refused else "captured_sends_not_one"
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("--mint", required=True)
    ap.add_argument("--public-non-r4bt-mint", action="store_true", required=True, help="statement: this mint is a public canonical mint and not an r4-b-T mint")
    ap.add_argument("--rpc", default=ss.DEFAULT_RPC, help="public RPC URL (no key in it)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if "?" in a.rpc or "api-key" in a.rpc.lower() or "@" in a.rpc:
        print("--rpc must be a plain public URL (no query string, no key)", file=sys.stderr)
        return 2
    try:
        rec = run(ss.RateLimitedRpc(a.rpc), a.mint)
    except hs.Refused as exc:
        print(json.dumps({"verdict": "REFUSED", "refusal": str(exc.code), "mint": a.mint}))
        return 2
    except SystemExit as exc:  # an RPC failure or "no ordinary holder": labels only, never a URL
        print(json.dumps({"verdict": "ERROR", "error": str(exc.code)[:300], "mint": a.mint}))
        return 3
    line = {k: rec[k] for k in ("mint", "token_program", "success", "err_class", "compute_units", "sent")}
    if a.out:
        a.out.write_text(json.dumps(rec, sort_keys=True, indent=1) + "\n")
    print(json.dumps(line, sort_keys=True))
    return 0 if rec["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
