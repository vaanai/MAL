"""On-chain metadata-uri resolution for graduated mints via PUBLIC mainnet RPC (no key, no Helius). PAPER ONLY, read-only RPC.

For each mint: (1) the Metaplex metadata PDA (seeds "metadata", metaplex program, mint) through getMultipleAccounts (base64), parsing
name / symbol / uri; (2) for mints with no such account, the mint account itself with encoding jsonParsed and the Token-2022 `tokenMetadata`
extension (state.uri). The uri JSON itself is fetched by hist.MetaFetcher (cached, rate limited).

CAVEAT: the account is read NOW (2026-11), not at creation. pump.fun revokes the update authority, so the uri is normally what it was at
creation; a mint whose authority was kept could have been edited. The uri JSON content is immutable for IPFS cids.
"""
from __future__ import annotations

import base64
import json
import struct
import time
import urllib.error
import urllib.request

METAPLEX = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
RPC_URL = "https://api.mainnet-beta.solana.com"
BATCH = 100


def pda(mint: str) -> str:
    from solders.pubkey import Pubkey
    prog = Pubkey.from_string(METAPLEX)
    return str(Pubkey.find_program_address([b"metadata", bytes(prog), bytes(Pubkey.from_string(mint))], prog)[0])


def parse_metaplex(data: bytes):
    """Metadata account: key u8, update_authority 32, mint 32, then name / symbol / uri as u32-length strings (NUL padded). None if malformed."""
    try:
        if len(data) < 69:
            return None
        off = 65
        out = []
        for _ in range(3):
            (n,) = struct.unpack_from("<I", data, off)
            off += 4
            if n > 400 or off + n > len(data):
                return None
            out.append(data[off:off + n].decode("utf-8", "replace").rstrip("\x00").strip())
            off += n
        return {"name": out[0], "symbol": out[1], "uri": out[2]}
    except Exception:
        return None


def parse_token2022(value):
    """value: one getMultipleAccounts jsonParsed entry. -> {name, symbol, uri} from the tokenMetadata extension, or None."""
    try:
        info = value["data"]["parsed"]["info"]
        for ext in info.get("extensions") or []:
            if ext.get("extension") == "tokenMetadata":
                st = ext.get("state") or {}
                if st.get("uri"):
                    return {"name": st.get("name"), "symbol": st.get("symbol"), "uri": str(st["uri"]).rstrip("\x00").strip()}
    except Exception:
        pass
    return None


class Rpc:
    """Minimal JSON-RPC client: global rate limit (default 4 req/s), retry with backoff on 429 / 5xx / transport errors."""

    def __init__(self, url=RPC_URL, min_interval_s=0.25, retries=5, timeout_s=20, opener=None, sleep=time.sleep, clock=time.monotonic):
        self.url, self.min_interval, self.retries, self.timeout = url, min_interval_s, retries, timeout_s
        self.opener, self.sleep, self.clock, self._last = opener or self._open, sleep, clock, 0.0
        self.calls = 0

    @staticmethod
    def _open(url, body, timeout):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "mal-gradscore-hist/0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.read().decode()

    def call(self, method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(self.retries):
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = self.clock()
            self.calls += 1
            try:
                doc = json.loads(self.opener(self.url, body, self.timeout))
                if "error" in doc:
                    raise RuntimeError("rpc_error")
                return doc["result"]
            except Exception:
                self.sleep(min(2 ** attempt, 20))
        return None


def resolve(mints, rpc, batch=BATCH):
    """-> {mint: {uri, name, symbol, source, rpc_status}}; rpc_status in ok | no_account | rpc_fail | no_uri."""
    out = {}
    mints = list(mints)
    for i in range(0, len(mints), batch):
        chunk = mints[i:i + batch]
        res = rpc.call("getMultipleAccounts", [[pda(m) for m in chunk], {"encoding": "base64"}])
        if res is None:
            for m in chunk:
                out[m] = {"uri": None, "name": None, "symbol": None, "source": None, "rpc_status": "rpc_fail"}
            continue
        rest = []
        for m, v in zip(chunk, res["value"]):
            p = parse_metaplex(base64.b64decode(v["data"][0])) if v else None
            if p and p["uri"]:
                out[m] = dict(p, source="metaplex", rpc_status="ok")
            else:
                rest.append(m)
        if rest:
            res2 = rpc.call("getMultipleAccounts", [rest, {"encoding": "jsonParsed"}])
            for m, v in zip(rest, (res2 or {}).get("value", [None] * len(rest))):
                if res2 is None:
                    out[m] = {"uri": None, "name": None, "symbol": None, "source": None, "rpc_status": "rpc_fail"}
                    continue
                p = parse_token2022(v) if v else None
                if p:
                    out[m] = dict(p, source="token2022", rpc_status="ok")
                else:
                    out[m] = {"uri": None, "name": None, "symbol": None, "source": None, "rpc_status": "no_account" if not v else "no_uri"}
    return out
