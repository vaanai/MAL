#!/usr/bin/env python3
"""Read-only watch of the pump Global account's block0_tax_bps (QP-RULING-1128 W1).

One public-RPC getAccountInfo (base64) of the pump Global PDA ["global"]. Prints ONE JSON line
{utc, slot, global_len, global_sha256, block0_tax_bps, status} and exits:
  0  rate == 0
  3  rate != 0 (W1 trigger: report to QP the same day)
  2  read or shape error (RPC failure, owner != pump program, length != 1104)
Layout (s37_t3_tax_idl_global.py): block0_tax_bps is u64 little-endian at bytes 1096..1104 of the 1104-byte account.
RPC URL from env PUMP_TAX_RPC (default public mainnet-beta; no key in code). Stdlib only; python3 -I compatible.
Does not gate FLOOR. Reports the Global sha256 for information only (it changes on any SetParams).
"""
import base64
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
GLOBAL_PDA = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"  # PDA ["global"] under pump, as s37 derived it
GLOBAL_LEN = 1104
RATE_OFF = 1096
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def evaluate(owner, raw, slot):
    """Pure: (record, exit_code) from the account owner, raw bytes and context slot."""
    rec = {"utc": utc_now(), "slot": slot, "global_len": None, "global_sha256": None, "block0_tax_bps": None, "status": "ERROR"}
    if raw is not None:
        rec["global_len"] = len(raw)
        rec["global_sha256"] = hashlib.sha256(raw).hexdigest()
    if owner != PUMP_PROGRAM:
        rec["status"] = "ERROR_OWNER"
        return rec, 2
    if raw is None or len(raw) != GLOBAL_LEN:
        rec["status"] = "ERROR_LEN"
        return rec, 2
    rate = int.from_bytes(raw[RATE_OFF:RATE_OFF + 8], "little")
    rec["block0_tax_bps"] = rate
    if rate == 0:
        rec["status"] = "OK_ZERO"
        return rec, 0
    rec["status"] = "NONZERO"
    return rec, 3


def fetch(url, timeout=30):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                       "params": [GLOBAL_PDA, {"encoding": "base64", "commitment": "finalized"}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "pump-tax-watch/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read())
    result = res["result"]
    val = result["value"]
    if val is None:
        return None, None, result["context"]["slot"]
    return val["owner"], base64.b64decode(val["data"][0]), result["context"]["slot"]


def main():
    url = os.environ.get("PUMP_TAX_RPC") or DEFAULT_RPC
    try:
        owner, raw, slot = fetch(url)
    except Exception as exc:  # network, JSON or shape error; never print the URL
        print(json.dumps({"utc": utc_now(), "slot": None, "global_len": None, "global_sha256": None, "block0_tax_bps": None,
                          "status": "ERROR_READ:" + type(exc).__name__}))
        return 2
    rec, code = evaluate(owner, raw, slot)
    print(json.dumps(rec))
    return code


if __name__ == "__main__":
    sys.exit(main())
