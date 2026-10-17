#!/usr/bin/env python3
"""Fetch mint-account structure (getAccountInfo) for a list of mints. Outcome-blind: owner program, authorities, decimals, supply and Token-2022
extension names are current chain state of public mint accounts. They say nothing about trades, prices or outcomes (D0-type identity/structure).

Modes
  census   input = a list of mints (text, one per line, or JSONL with a "mint" field). Output: mints.jsonl + summary.json.
  members  input = the walk-2 member list (same formats). Same fetch; additionally writes mint_accounts.json in the schema tools/r4b_read.py loads
           ({fetch_slot, mints: {mint: {fetch_ok, owner_program, freeze_authority, token2022_extensions}}}). Implemented, NOT run at census time.

The RPC URL is built at run time from HELIUS_API_KEY in the env (it wins) or in the Helius env file (default /var/lib/mal/backfill/helius.env). The key and URL
are never printed, logged or written; error strings are redacted. Rate limit <= 10 rps (one in-flight call, 0.1 s spacing), 3 attempts per call.
Each getAccountInfo is one call; credits ~= calls. jsonParsed is tried first, base64 is the fallback (parsed by hand when the node cannot parse).

  /data/mal/venv/bin/python -I tools/mint_account_fetch.py census --mints IN.txt --out-dir DIR
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import struct
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Callable

HELIUS_ENV_FILE = Path("/var/lib/mal/backfill/helius.env")
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
MAX_RPS = 10.0
ATTEMPTS = 3
TIMEOUT_S = 20
_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_ALPHA = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# Token-2022 ExtensionType ids -> names as returned by jsonParsed (camelCase), for the base64 fallback.
EXT_NAMES = {
    0: "uninitialized", 1: "transferFeeConfig", 2: "transferFeeAmount", 3: "mintCloseAuthority", 4: "confidentialTransferMint",
    5: "confidentialTransferAccount", 6: "defaultAccountState", 7: "immutableOwner", 8: "memoTransfer", 9: "nonTransferable",
    10: "interestBearingConfig", 11: "cpiGuard", 12: "permanentDelegate", 13: "nonTransferableAccount", 14: "transferHook",
    15: "transferHookAccount", 16: "confidentialTransferFeeConfig", 17: "confidentialTransferFeeAmount", 18: "metadataPointer",
    19: "tokenMetadata", 20: "groupPointer", 21: "tokenGroup", 22: "groupMemberPointer", 23: "tokenGroupMember",
    24: "confidentialMintBurn", 25: "scaledUiAmountConfig", 26: "pausableConfig", 27: "pausableAccount",
}


class FetchError(Exception):
    pass


def load_key(env: dict | None = None, env_file: Path = HELIUS_ENV_FILE) -> str:
    env = os.environ if env is None else env
    key = (env.get("HELIUS_API_KEY") or "").strip()
    if not key:
        try:
            for line in Path(env_file).read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, val = line.partition("=")
                if name.strip().removeprefix("export ").strip() == "HELIUS_API_KEY":
                    key = val.strip().strip("'\"")
        except OSError:
            raise FetchError("helius env file unreadable") from None
    if not key or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise FetchError("HELIUS_API_KEY missing or malformed")
    return key


def rpc_url(key: str) -> str:
    return f"https://mainnet.helius-rpc.com/?api-key={key}"


def redact(text: str, secrets: list[str]) -> str:
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "<redacted>")
    out = re.sub(r"https?://\S+", "<url>", out)
    return re.sub(r"api-key=[^&\s]+", "api-key=<redacted>", out)


def http_post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


class Fetcher:
    def __init__(self, url: str, post: Callable[[str, dict], dict] = http_post, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, secrets: list[str] | None = None, backoff: float = 1.0):
        self.url, self.post, self.sleep, self.clock = url, post, sleep, clock
        self.secrets = [url] + list(secrets or [])
        self.backoff = backoff
        self.calls = 0
        self._last = None

    def _pace(self):
        if self._last is not None:
            wait = 1.0 / MAX_RPS - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def call(self, mint: str, encoding: str) -> dict:
        last = "unknown"
        for attempt in range(ATTEMPTS):
            self._pace()
            self.calls += 1
            payload = {"jsonrpc": "2.0", "id": self.calls, "method": "getAccountInfo",
                       "params": [mint, {"encoding": encoding, "commitment": "finalized"}]}
            try:
                resp = self.post(self.url, payload)
                if isinstance(resp, dict) and resp.get("error"):
                    raise FetchError("rpc error " + str((resp["error"] or {}).get("code")))
                if not isinstance(resp, dict) or "result" not in resp:
                    raise FetchError("malformed rpc response")
                return resp["result"]
            except Exception as e:  # noqa: BLE001 - redacted below
                last = redact(f"{type(e).__name__}: {e}", self.secrets)
                if attempt + 1 < ATTEMPTS:
                    self.sleep(self.backoff * (2 ** attempt))
        raise FetchError(last)


def _b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _ALPHA[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def _coption(b: bytes, off: int) -> str | None:
    if struct.unpack_from("<I", b, off)[0] == 0:
        return None
    return _b58encode(b[off + 4: off + 36])


def parse_mint_base64(raw: bytes, owner: str) -> dict:
    """Mint layout: mint_authority COption<Pubkey> @0, supply u64 @36, decimals u8 @44, is_initialized @45, freeze COption @46 (82 bytes).
    Token-2022: account-type byte @165, then TLV (u16 type, u16 len, value) from 166."""
    if len(raw) < 82:
        raise FetchError("account data shorter than a mint")
    exts: list[str] = []
    if owner == TOKEN_2022_PROGRAM and len(raw) > 165:
        if raw[165] != 1:
            raise FetchError("token-2022 account is not a mint")
        i = 166
        while i + 4 <= len(raw):
            t, ln = struct.unpack_from("<HH", raw, i)
            if t == 0 and ln == 0:
                break
            exts.append(EXT_NAMES.get(t, f"unknown{t}"))
            i += 4 + ln
    return {"mint_authority": _coption(raw, 0), "supply": str(struct.unpack_from("<Q", raw, 36)[0]), "decimals": raw[44],
            "freeze_authority": _coption(raw, 46), "token2022_extensions": exts}


def from_parsed(value: dict) -> dict | None:
    data = value.get("data")
    if not isinstance(data, dict) or data.get("program") not in ("spl-token", "spl-token-2022"):
        return None
    p = data.get("parsed") or {}
    if p.get("type") != "mint":
        raise FetchError("account is not a mint")
    info = p.get("info") or {}
    return {"mint_authority": info.get("mintAuthority"), "supply": info.get("supply"), "decimals": info.get("decimals"),
            "freeze_authority": info.get("freezeAuthority"),
            "token2022_extensions": [str(e.get("extension")) for e in (info.get("extensions") or [])]}


def fetch_one(f: Fetcher, mint: str) -> dict:
    rec = {"mint": mint, "owner_program": None, "fetch_slot": None, "freeze_authority": None, "mint_authority": None, "decimals": None,
           "supply": None, "token2022_extensions": [], "fetch_ok": False, "error": None, "encoding": None}
    try:
        res = f.call(mint, "jsonParsed")
        value = (res or {}).get("value")
        if value is None:
            raise FetchError("account not found")
        rec["fetch_slot"] = (res.get("context") or {}).get("slot")
        rec["owner_program"] = value.get("owner")
        parsed = from_parsed(value)
        rec["encoding"] = "jsonParsed"
        if parsed is None:  # node could not parse: fall back to raw base64
            res2 = f.call(mint, "base64")
            v2 = (res2 or {}).get("value")
            if v2 is None:
                raise FetchError("account not found")
            rec["fetch_slot"] = (res2.get("context") or {}).get("slot")
            rec["owner_program"] = v2.get("owner")
            d = v2.get("data")
            if not (isinstance(d, list) and len(d) == 2 and d[1] == "base64"):
                raise FetchError("unexpected base64 shape")
            parsed = parse_mint_base64(base64.b64decode(d[0]), rec["owner_program"])
            rec["encoding"] = "base64"
        rec.update(parsed)
        if not isinstance(rec["fetch_slot"], int):
            raise FetchError("no context slot")
        rec["fetch_ok"] = True
    except Exception as e:  # noqa: BLE001
        rec["error"] = redact(f"{type(e).__name__}: {e}", f.secrets)[:200]
    rec["ok"] = rec["fetch_ok"]
    return rec


def summarize(recs: list[dict], calls: int, mode: str) -> dict:
    ok = [r for r in recs if r["fetch_ok"]]
    ext_mints: Counter = Counter()
    ext_sets: Counter = Counter()
    for r in ok:
        s = set(r["token2022_extensions"])
        ext_mints.update(s)
        ext_sets[tuple(sorted(s))] += 1
    return {"mode": mode, "mints": len(recs), "ok": len(ok), "error_count": len(recs) - len(ok),
            "owner_program_counts": dict(Counter(r["owner_program"] for r in ok)),
            "extension_type_frequency": dict(sorted(ext_mints.items(), key=lambda kv: -kv[1])),
            "extension_set_frequency": {"+".join(k) or "(none)": v for k, v in ext_sets.most_common()},
            "freeze_authority_present": sum(1 for r in ok if r["freeze_authority"]),
            "mint_authority_present": sum(1 for r in ok if r["mint_authority"]),
            "encoding_counts": dict(Counter(r["encoding"] for r in ok)),
            "fetch_slot_min": min((r["fetch_slot"] for r in ok), default=None),
            "fetch_slot_max": max((r["fetch_slot"] for r in ok), default=None),
            "calls": calls, "credits_approx": calls, "outcome_blind": True}


def read_mints(path: Path) -> list[str]:
    out: list[str] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        mint = json.loads(line).get("mint") if line.startswith("{") else line
        if not isinstance(mint, str) or not _B58.match(mint):
            raise ValueError("bad mint in input")
        out.append(mint)
    return list(dict.fromkeys(out))


def run(mode: str, mints: list[str], out_dir: Path, fetcher: Fetcher) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = []
    with open(out_dir / "mints.jsonl", "w") as fh:
        for mint in mints:
            r = fetch_one(fetcher, mint)
            recs.append(r)
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
    summ = summarize(recs, fetcher.calls, mode)
    (out_dir / "summary.json").write_text(json.dumps(summ, indent=2, sort_keys=True) + "\n")
    if mode == "members":
        slots = [r["fetch_slot"] for r in recs if r["fetch_ok"]]
        doc = {"fetch_slot": max(slots) if slots else None,
               "mints": {r["mint"]: {k: r[k] for k in ("fetch_ok", "owner_program", "freeze_authority", "token2022_extensions")} for r in recs}}
        (out_dir / "mint_accounts.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return summ


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["census", "members"])
    ap.add_argument("--mints", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--env-file", type=Path, default=HELIUS_ENV_FILE)
    a = ap.parse_args(argv)
    try:
        key = load_key(env_file=a.env_file)
    except FetchError as e:
        print("error:", e, file=sys.stderr)
        return 2
    f = Fetcher(rpc_url(key), secrets=[key])
    summ = run(a.mode, read_mints(a.mints), a.out_dir, f)
    print(json.dumps({k: summ[k] for k in ("mode", "mints", "ok", "error_count", "calls", "credits_approx")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
