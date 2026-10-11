"""DEC-026 item 14(c), route B (quant-proof's ruling on PR #607): C1NFExecutor in LIVE mode, fully offline, on a stub chain.

What it does. It feeds a pick file to the REAL `tools/c1nf_executor.py` (`C1NFExecutor(stub_rpc, cfg, keypair, now_ms=...)`, live mode, an in-memory
ephemeral keypair, a deterministic stub RPC, no socket) and writes one line per pick, sorted:

    mint,decision_T_ms,accepted|skipped,reason          line count = pick count

`tools/c1nf_item14c_reference.py` (no executor import) applies DEC-026 section 6's limits to the same picks and the same stub exits;
`prove` runs the executor twice (two processes) and the reference once, and passes only if md5(executor) == md5(reference) and the second executor run
is byte-equal to the first. This tests the executor's accept / skip logic (the 0.05 SOL stake, 2 open, 30 a day, the daily stop, the 0.175 SOL total
stop, end_ms, already_held, the 60 s cooldown). It is not an edge test: the stub's "exits" are synthetic returns, a test fixture, and no P&L, label,
tape or outcome file is read.

The six conditions of QP's ruling, and where each is enforced
  1 closure     `--closure-dir` must be a DETACHED git checkout whose HEAD is INSTALL_SHA (f873aac...), clean, and the two files that are imported must hash
                (git blob sha1, recomputed from the bytes) to BLOBS (c1nf_executor 679f4da2..., h5_executor b189dbd1...). Checked before the import and again on
                the imported modules' __file__. This driver, the stub and the reference live in tools/ of the branch, outside the closure; no closure file is edited.
  2 key         `Keypair()` in memory; never written, logged, printed, put in a config or passed to main()/credential_path(). The unpatched
                C1NF_WALLET_PUBKEY and H5_WALLET_PUBKEY are recorded and the ephemeral pubkey is asserted different from both BEFORE any patch.
                CREDENTIALS_DIRECTORY is unset (asserted). After the run every file under the work dir is scanned for the secret in 9 encodings: 0 hits.
  3 network     the only RPC is the stub callable. socket.socket.connect / connect_ex, socket.create_connection, getaddrinfo and gethostbyname raise and
                are counted: the count must be 0 at the end. HELIUS* / RPC* / SOLANA* env variables are removed (names recorded).
  4 patches     module-attribute patches only (the kind the executor's own tests use), each recorded in the meta: LIVE_STATE_DIR, LIVE_OK_PATH,
                TIER_FILE_PATH, C1NF_WALLET_PUBKEY, PICK_WINDOW_START_MS (the pre_window patch), h5.LIVE_OK_UID / LIVE_OK_GID (this process stands in for
                root, as the tests do), probe_executor.LIVE_DIR. Nothing touches a limit table, _pick_refusal, _budget_stop, _balance_refusal, _seal_reason or the
                reason set. The feed heartbeat is delivered through the executor's own `_on_heartbeat`, as intent_tick does.
  5 io          input: the 09-20 subset of the x594h-0920 picks (`extract` writes it once, with a sha256 sidecar). Output and md5 rule as above.
  6 coverage    the stub's exits (a fixed function of (mint, pick index); `loss-first-v1`) are built to fire the total stop and max_open on a realistic day; the
                run reports the reasons that occurred (`--require-reasons` fails the proof if one did not). `synthetic-limits-v1` is a second, synthetic pick file
                that fires 30/day, end_ms, already_held, the cooldown, max_open and the daily stop.

Commands
  extract    --picks-dir D --day 2026-09-20 --out F     the day's c1nf_pick rows (whitelisted fields only), sorted, with F.sha256
  synth      --closure-dir C --kind limits|realistic --out F
  prove      --closure-dir C --picks F --work-dir W [--profile loss-first-v1|synthetic-limits-v1] [--end-ms N] [--require-reasons a,b]
  run        (internal) one executor run, in its own process
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import socket
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

INSTALL_SHA = "f873aac3a07b52ba03c0c13db675051a88e21b38"
BLOBS = {"tools/c1nf_executor.py": "679f4da2c48d103afa3eb84e613cedfb9b94dec2", "tools/h5_executor.py": "b189dbd192a929e161f5c29d95b2ebe008a67a32"}
CANARY_MODEL_SHA = "faf8a01f5fb5019a3c26affc7de49f47270e7b6717eea34767bfcdc759399478"
PICK_FIELDS = ("type", "mint", "pool", "decision_T_ms", "SD_slot", "pred", "h_top1", "stage1", "feature_hash", "model_sha", "q_lamports", "base_reserve",
               "v_lamports", "state_slot", "t_ms")
ENV_PREFIXES = ("HELIUS", "RPC", "SOLANA", "CREDENTIALS_DIRECTORY")
DAY_MS = 86_400_000
MIN_MS = 60_000

# --- the stub world (all of it a fixed function of (mint, pick index); recorded in the spec and hashed) --------------------------------------------
PROFILE_LOSS_FIRST = "loss-first-v1"
PROFILE_SYNTH = "synthetic-limits-v1"
PROFILES = (PROFILE_LOSS_FIRST, PROFILE_SYNTH)
STUB_CONSTANTS = {
    "stake_lamports": 50_000_000, "wallet0_lamports": 500_000_000, "fee_buy_lamports": 510_000, "fee_sell_lamports": 510_000, "ata_rent_lamports": 2_039_280,
    "slot_ms": 400, "chain_t0_ms": 1_789_776_000_000, "slot_base": 400_000_000, "receipt_after_decision_ms": 600, "buy_land_ms": 700, "sell_land_ms": 900,
    "close_after_receipt_ms": 301_600,  # buy sent at receipt, landed +700; exit anchored on the landed slot's start; sell sent at anchor + 300.55 s - 0.5 s, lands +900
    "timing_tolerance_ms": 1_000, "block_height": 900, "last_valid_block_height": 10**9, "model_sha": CANARY_MODEL_SHA,
}
DAY0 = 1_789_862_400_000  # 2026-09-20T00:00:00Z


def _h32(*parts: str) -> bytes:
    return hashlib.sha256("/".join(parts).encode()).digest()


def ret_loss_first(mint: str, idx: int) -> int:
    """Synthetic exit return in bps of the stake. The first 8 picks lose 86..90% (the total stop is reached after three closed losers: realized -0.138 with a
    0.05 stake at risk is below -0.175), later picks mostly lose 10..50% and one in six wins 15..40%."""
    h = int.from_bytes(_h32("c1nf-item14c", PROFILE_LOSS_FIRST, mint, str(idx))[:4], "big")
    if idx < 8:
        return -(8600 + (h % 5) * 100)
    if idx % 6 == 5:
        return 1500 + h % 2500
    return -(1000 + h % 4000)


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n, s = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def synth_mint(label: str) -> str:
    return b58encode(_h32("c1nf-item14c-synthetic-mint", label))


def synthetic_limits_layout() -> list[tuple[int, str, int]]:
    """(decision_T_ms, mint, ret_bps) in sorted pick order. D1 (09-20): three picks in one minute (two accepted, the third max_open), the held mint again
    a minute later (already_held), again 6 minutes in (cooldown), again at 7 (accepted); then 29 picks 6 minutes apart at +16% (27 accepted: 30 attempts
    with the first three, then max_trades_day). D2: eight losers 6 minutes apart (four accepted, then daily_loss_stop; the tier total stays above -0.175
    because D1 made money). D3: benign picks; the config end_ms is D3 03:00Z (the picks from then on are end_instant)."""
    a = sorted(synth_mint(f"A{k}") for k in range(3))
    rows: list[tuple[int, str, int]] = [(DAY0 + 60 * MIN_MS, m, 0) for m in a]
    held = a[0]
    rows += [(DAY0 + 61 * MIN_MS, held, 0), (DAY0 + 66 * MIN_MS, held, 0), (DAY0 + 67 * MIN_MS, held, 0)]
    rows += [(DAY0 + (120 + 6 * k) * MIN_MS, synth_mint(f"B{k}"), 1600) for k in range(29)]
    rows += [(DAY0 + DAY_MS + (60 + 6 * k) * MIN_MS, synth_mint(f"C{k}"), -9000) for k in range(8)]
    rows += [(DAY0 + 2 * DAY_MS + (60 + 6 * k) * MIN_MS, synth_mint(f"E{k}"), 0) for k in range(41)]
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


SYNTH_LIMITS_END_MS = DAY0 + 2 * DAY_MS + 180 * MIN_MS


def realistic_layout() -> list[tuple[int, str]]:
    """A day-shaped list of 28 picks with hot clusters (09-20): the loss-first profile fires max_open and the total stop on it."""
    minutes = [3, 3, 4, 25, 40, 41, 44, 70, 71, 95, 96, 97, 130, 150, 151, 190, 191, 230, 231, 270, 271, 310, 311, 350, 351, 400, 401, 450]
    return sorted((DAY0 + m * MIN_MS, synth_mint(f"R{i}")) for i, m in enumerate(minutes))


# --- pure helpers (no closure import) -------------------------------------------------------------------------------------------------------------
def blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_pick_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    rows = [r for r in rows if isinstance(r, dict) and r.get("type") == "c1nf_pick"]
    rows.sort(key=lambda r: (int(r["decision_T_ms"]), str(r["mint"])))
    return rows


def build_stub_spec(rows: list[dict[str, Any]], profile: str) -> dict[str, Any]:
    if profile not in PROFILES:
        raise SystemExit(f"unknown stub profile {profile!r}")
    c = STUB_CONSTANTS
    table = None
    if profile == PROFILE_SYNTH:
        layout = synthetic_limits_layout()
        if [(int(r["decision_T_ms"]), str(r["mint"])) for r in rows] != [(t, m) for t, m, _r in layout]:
            raise SystemExit("the synthetic-limits profile only describes the synthetic-limits file")
        table = [r[2] for r in layout]
    picks = []
    for i, r in enumerate(rows):
        t, mint = int(r["decision_T_ms"]), str(r["mint"])
        if table is not None:
            if i >= len(table):
                raise SystemExit("the synthetic-limits profile only describes the synthetic-limits file")
            ret = table[i]
        else:
            ret = ret_loss_first(mint, i)
        rc = t + c["receipt_after_decision_ms"]
        picks.append({"idx": i, "mint": mint, "decision_T_ms": t, "receipt_ms": rc, "ret_bps": ret, "close_ms": rc + c["close_after_receipt_ms"]})
    return {"schema": "c1nf_item14c_stub_spec_v1", "profile": profile, "constants": dict(c), "picks": picks}


def secret_encodings(secret64: bytes) -> list[bytes]:
    seed = secret64[:32]
    out = [secret64, seed, secret64.hex().encode(), seed.hex().encode(), base64.b64encode(secret64), base64.b64encode(seed),
           json.dumps(list(secret64)).encode(), json.dumps(list(secret64), separators=(",", ":")).encode()]
    n, s = int.from_bytes(secret64, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    out.append(s.encode())
    return out


def scan_for_secret(root: Path, secret64: bytes) -> dict[str, int]:
    needles = secret_encodings(secret64)
    hits = files = nbytes = 0
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            data = p.read_bytes()
            files, nbytes = files + 1, nbytes + len(data)
            hits += sum(data.count(n) for n in needles)
    return {"files_scanned": files, "bytes_scanned": nbytes, "encodings": len(needles), "hits": hits}


class NetGuard:
    """Every socket entry point raises and counts. Used as a context manager around the whole run."""

    NAMES = (("socket.socket", "connect"), ("socket.socket", "connect_ex"), ("socket", "create_connection"), ("socket", "getaddrinfo"), ("socket", "gethostbyname"))

    def __init__(self) -> None:
        self.calls = 0
        self._saved: list[tuple[Any, str, Any]] = []

    def _raiser(self, name: str):
        def fn(*_a: Any, **_k: Any) -> Any:
            self.calls += 1
            raise OSError(f"network is blocked in the 14(c) driver: {name}")
        return fn

    def __enter__(self) -> "NetGuard":
        for owner, attr in self.NAMES:
            obj = socket.socket if owner == "socket.socket" else socket
            self._saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, self._raiser(f"{owner}.{attr}"))
        return self

    def __exit__(self, *_a: Any) -> None:
        for obj, attr, old in reversed(self._saved):
            setattr(obj, attr, old)


# --- the closure -----------------------------------------------------------------------------------------------------------------------------------
def _git(closure: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(closure), *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise SystemExit(f"closure check refused: git {' '.join(args)} failed")
    return r.stdout.strip()


def check_closure(closure: Path) -> dict[str, Any]:
    """Condition 1. A detached, clean checkout of INSTALL_SHA whose two executor files are the pinned blobs."""
    if not (closure / ".git").exists():
        raise SystemExit("closure check refused: --closure-dir is not a git checkout")
    head = _git(closure, "rev-parse", "HEAD")
    if head != INSTALL_SHA:
        raise SystemExit(f"closure check refused: HEAD {head} is not {INSTALL_SHA}")
    if subprocess.run(["git", "-C", str(closure), "symbolic-ref", "-q", "HEAD"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("closure check refused: the checkout is on a branch, not detached")
    if _git(closure, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("closure check refused: the checkout has local changes")
    blobs = {}
    for rel, want in BLOBS.items():
        got = blob_sha1((closure / rel).read_bytes())
        if got != want:
            raise SystemExit(f"closure check refused: {rel} is blob {got}, not {want}")
        blobs[rel] = got
    return {"head": head, "detached": True, "clean": True, "blobs": blobs}


def load_closure(closure: Path) -> SimpleNamespace:
    closure = closure.resolve()
    info = check_closure(closure)
    if "tools" in sys.modules:
        raise SystemExit("closure check refused: a `tools` package is already imported")
    sys.path.insert(0, str(closure))
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from tools import c1nf_executor as c
    from tools import h5_executor as h5
    from tools import probe_executor as pe
    from tools import pumpswap_tx as tx

    for mod, rel in ((c, "tools/c1nf_executor.py"), (h5, "tools/h5_executor.py")):
        f = Path(mod.__file__).resolve()
        if f != (closure / rel).resolve():
            raise SystemExit(f"closure check refused: {rel} was imported from {f}")
        if blob_sha1(f.read_bytes()) != BLOBS[rel]:
            raise SystemExit(f"closure check refused: the imported {rel} is not the pinned blob")
    return SimpleNamespace(closure=closure, info=info, c=c, h5=h5, pe=pe, tx=tx, Keypair=Keypair, Pubkey=Pubkey)


# --- the stub chain ----------------------------------------------------------------------------------------------------------------------------------
class StubError(RuntimeError):
    pass


class Clock:
    def __init__(self, t: int) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


class StubChain:
    """The deterministic RPC. Quotes (the reserves of each pick's own decision state), sends, confirmations and fills are a fixed function of the pick
    (mint, index) and the clock. Any method it does not know raises (nothing is silently answered)."""

    def __init__(self, M: SimpleNamespace, clock: Clock, user: Any, rows: list[dict[str, Any]], spec: dict[str, Any]) -> None:
        self.M, self.clock, self.user = M, clock, user
        self.k = spec["constants"]
        self.spec_picks = spec["picks"]
        self.ex: Any = None
        self.counts: Counter[str] = Counter()
        self.fetches = 0
        self.sent: dict[str, dict[str, Any]] = {}
        self.rebroadcasts = 0
        fx = json.loads((M.closure / "tools/fixtures/pumpswap/buy_exact_quote_in_a.json").read_text())
        self.gc_b64 = json.loads((M.closure / "tools/fixtures/pumpswap/global_config.json").read_text())["data_b64"]
        pool0 = bytearray(base64.b64decode(fx["pool_account_b64"]))
        self.V = M.tx.parse_pool_account(bytes(pool0))["virtual_quote_reserves"]
        self.pools: dict[str, dict[str, Any]] = {}
        self.accts: dict[str, dict[str, Any]] = {}
        self.by_pick_id: dict[str, int] = {}
        tok2022 = str(M.tx.TOKEN_2022_PROGRAM)
        for i, r in enumerate(rows):
            mint, pk = str(r["mint"]), M.Pubkey.from_string(str(r["mint"]))
            pool = str(M.tx.canonical_pool(pk))
            ppk = M.Pubkey.from_string(pool)
            bv = str(M.tx.ata(ppk, pk, M.tx.TOKEN_2022_PROGRAM))  # the executor refuses vaults that are not the pool's derived token accounts
            qv = str(M.tx.ata(ppk, M.tx.WSOL_MINT, M.tx.TOKEN_PROGRAM))
            data = bytearray(pool0)
            data[43:75], data[139:171], data[171:203] = bytes(pk), bytes(M.Pubkey.from_string(bv)), bytes(M.Pubkey.from_string(qv))
            q, b = int(r["q_lamports"]), int(r["base_reserve"])
            if q <= self.V:
                raise StubError("a pick's q_lamports is not above the fixture pool's V")
            if mint not in self.pools:
                self.pools[mint] = {"pool": pool, "q_vault": q - self.V, "base": b, "q": q, "ata": str(M.tx.ata(user, pk, M.tx.TOKEN_2022_PROGRAM))}
                self.accts[pool] = {"owner": str(M.tx.PUMPSWAP_PROGRAM), "data": [base64.b64encode(bytes(data)).decode(), "base64"], "lamports": 1}
                self.accts[mint] = {"owner": tok2022, "data": ["", "base64"], "lamports": 1}
                self.accts[bv] = self._tok_acct(b)
                self.accts[qv] = self._tok_acct(q - self.V)
            self.by_pick_id[f"{mint}:{int(r['decision_T_ms'])}"] = i
        self.accts[str(M.tx.GLOBAL_CONFIG)] = {"owner": "x", "data": [self.gc_b64, "base64"], "lamports": 1}

    @staticmethod
    def _tok_acct(amount: int) -> dict[str, Any]:
        d = bytearray(165)
        d[64:72] = amount.to_bytes(8, "little")
        return {"owner": "x", "data": [base64.b64encode(bytes(d)).decode(), "base64"], "lamports": 2_039_280}

    def slot_at(self, wall_ms: int) -> int:
        return self.k["slot_base"] + (wall_ms - self.k["chain_t0_ms"]) // self.k["slot_ms"]

    def _landed(self, s: dict[str, Any]) -> bool:
        return self.clock() >= s["land_ms"]

    def balance(self) -> int:
        return self.k["wallet0_lamports"] + sum(s["delta"] for s in self.sent.values() if self._landed(s))

    def _register(self, sig: str) -> dict[str, Any]:
        ex, k = self.ex, self.k
        for mint, p in ex.state.pending.items():
            if p.get("signature") != sig:
                continue
            kind = p["kind"]
            pos = ex.state.open.get(mint)
            trig = (p.get("h5") or {}).get("trigger") if kind == "buy" else ((pos or {}).get("h5") or {}).get("trigger")
            if not trig:
                raise StubError("a sent transaction has no pick behind it")
            idx = self.by_pick_id[trig["pick_id"]]
            now, info = self.clock(), self.pools[mint]
            tokens = self.M.h5.entry_terms(info["q"], info["base"], k["stake_lamports"], 1500)["expected_tokens"]
            if kind == "buy":
                if p["q_tokens"] != tokens:
                    raise StubError("the executor's quote differs from the stub's")
                delta, land = -(k["stake_lamports"] + k["fee_buy_lamports"] + k["ata_rent_lamports"]), now + k["buy_land_ms"]
            else:
                ret = self.spec_picks[idx]["ret_bps"]
                proceeds = k["stake_lamports"] + (k["stake_lamports"] * ret) // 10_000
                delta, land = proceeds + k["ata_rent_lamports"] - k["fee_sell_lamports"], now + k["sell_land_ms"]
            s = {"sig": sig, "kind": kind, "mint": mint, "idx": idx, "first_ms": now, "land_ms": land, "delta": delta, "tokens": tokens}
            self.sent[sig] = s
            return s
        raise StubError("a sent transaction is not any pending record")

    def _held(self, ata: str) -> int:
        for mint, info in self.pools.items():
            if info["ata"] == ata:
                got = sum(s["tokens"] if s["kind"] == "buy" else -s["tokens"] for s in self.sent.values() if s["mint"] == mint and self._landed(s))
                return max(0, got)
        raise StubError("unknown token account")

    def __call__(self, method: str, params: list) -> Any:
        self.counts[method] += 1
        M, now = self.M, self.clock()
        if method == "getSlot":
            return self.slot_at(now)
        if method == "getBalance":
            return {"context": {"slot": self.slot_at(now)}, "value": self.balance()}
        if method == "getLatestBlockhash":
            self.fetches += 1
            from solders.hash import Hash
            return {"context": {"slot": 1}, "value": {"blockhash": str(Hash(self.fetches.to_bytes(32, "little"))), "lastValidBlockHeight": self.k["last_valid_block_height"]}}
        if method == "getBlockHeight":
            return self.k["block_height"]
        if method == "getAccountInfo":
            return {"context": {"slot": self.slot_at(now)}, "value": self.accts[params[0]]}
        if method == "getMultipleAccounts":
            return {"context": {"slot": self.slot_at(now)}, "value": [self.accts[key] for key in params[0]]}
        if method == "getTokenAccountBalance":
            return {"value": {"amount": str(self._held(params[0]))}}
        if method == "sendTransaction":
            from solders.transaction import VersionedTransaction
            sig = str(VersionedTransaction.from_bytes(base64.b64decode(params[0])).signatures[0])
            if sig in self.sent:
                self.rebroadcasts += 1
            else:
                self._register(sig)
            return sig
        if method == "getSignatureStatuses":
            out = []
            for sig in params[0]:
                s = self.sent.get(sig)
                out.append({"slot": self.slot_at(s["land_ms"]), "confirmationStatus": "confirmed", "err": None} if s and self._landed(s) else None)
            return {"context": {"slot": 1}, "value": out}
        if method == "getTransaction":
            s = self.sent.get(params[0])
            if not s or not self._landed(s):
                return None
            return self._tx_result(s)
        raise StubError(f"stub RPC: unexpected method {method}")

    def _tx_result(self, s: dict[str, Any]) -> dict[str, Any]:
        k, M = self.k, self.M
        info = self.pools[s["mint"]]
        keys = [str(self.user), info["ata"]]
        buy = s["kind"] == "buy"
        row = [{"accountIndex": 1, "mint": s["mint"], "owner": str(self.user), "uiTokenAmount": {"amount": str(s["tokens"])}}]
        bal0 = k["wallet0_lamports"]
        return {"slot": self.slot_at(s["land_ms"]), "transaction": {"signatures": [s["sig"]], "message": {"accountKeys": keys}},
                "meta": {"err": None, "fee": k["fee_buy_lamports"] if buy else k["fee_sell_lamports"], "preBalances": [bal0, 0 if buy else k["ata_rent_lamports"]],
                         "postBalances": [bal0 + s["delta"], k["ata_rent_lamports"] if buy else 0], "preTokenBalances": [] if buy else row,
                         "postTokenBalances": row if buy else [], "logMessages": []}}


# --- one executor run ---------------------------------------------------------------------------------------------------------------------------------
def parse_reason_lines(text: str) -> Counter:
    return Counter(line.split(",", 3)[3] for line in text.splitlines() if line.split(",", 3)[2] == "skipped")


def run_once(closure: Path, picks_path: Path, spec_path: Path, work: Path, end_ms: int | None) -> dict[str, Any]:
    if work.exists():
        raise SystemExit(f"refused: {work} exists")
    removed = sorted(n for n in os.environ if n.startswith(ENV_PREFIXES))
    for n in removed:
        del os.environ[n]
    assert "CREDENTIALS_DIRECTORY" not in os.environ
    M = load_closure(closure)
    c, h5, pe = M.c, M.h5, M.pe
    unpatched = {"C1NF_WALLET_PUBKEY": c.C1NF_WALLET_PUBKEY, "H5_WALLET_PUBKEY": c.H5_WALLET_PUBKEY}
    kp = M.Keypair()  # in memory only
    eph = str(kp.pubkey())
    if eph in unpatched.values():
        raise SystemExit("the ephemeral key equals a pinned wallet")  # before any patch
    secret = bytes(kp)
    rows = read_pick_rows(picks_path)
    spec = json.loads(spec_path.read_text())
    if len(spec["picks"]) != len(rows):
        raise SystemExit("spec and picks differ in length")
    k = spec["constants"]
    work.mkdir(parents=True)
    etc, state, probe = work / "etc", work / "state", work / "probe-live"
    for d in (etc, probe):
        d.mkdir()
    os.chmod(etc, 0o755)
    (etc / "LIVE_OK").write_text("")
    os.chmod(etc / "LIVE_OK", 0o644)
    (etc / "TIER").write_text("T1\n")
    os.chmod(etc / "TIER", 0o644)
    patches = [(c, "LIVE_STATE_DIR", state), (c, "LIVE_OK_PATH", etc / "LIVE_OK"), (c, "TIER_FILE_PATH", etc / "TIER"), (c, "C1NF_WALLET_PUBKEY", eph),
               (c, "PICK_WINDOW_START_MS", 0), (h5, "LIVE_OK_UID", os.getuid()), (h5, "LIVE_OK_GID", os.getgid()), (pe, "LIVE_DIR", probe)]
    from unittest import mock
    clock = Clock(rows[0]["decision_T_ms"] - 3_600_000 if rows else k["chain_t0_ms"] + 3_600_000)
    cfg: dict[str, Any] = {"intents_file": str(work / "shadow"), "state_dir": str(state), "mode": "live", "poll_s": 5.0, "stake_lamports": k["stake_lamports"]}
    if end_ms is not None:
        cfg["end_ms"] = end_ms
    (work / "shadow").mkdir()
    out: list[tuple[str, int, str, str]] = []
    audit: dict[str, Any] = {"close_dev_ms_max": 0, "closes": []}
    with NetGuard() as net, contextlib.ExitStack() as stack:
        for target, attr, val in patches:
            stack.enter_context(mock.patch.object(target, attr, val))
        stub = StubChain(M, clock, kp.pubkey(), rows, spec)
        ex = c.C1NFExecutor(stub, cfg, kp, now_ms=clock, root=M.closure)
        stub.ex = ex
        drv = Driver(M, ex, stub, clock, spec)
        for i, r in enumerate(rows):
            out.append(drv.handle(i, r))
        drv.drain()
        audit = drv.audit
        halts = sorted(ex.counters.halts)
        opened = dict(ex.state.open), dict(ex.state.pending)
    final = {pid: p["status"] for pid, p in ex.extra.picks.items()}
    lines = "".join(f"{m},{t},{a},{why}\n" for m, t, a, why in sorted(out))
    (work / "lines.csv").write_text(lines)
    leaks = scan_for_secret(work, secret)
    meta = {
        "schema": "c1nf_item14c_run_v1", "closure": {**M.info, "path": str(M.closure)}, "python": sys.version.split()[0],
        "picks": {"path": str(picks_path), "sha256": sha256_file(picks_path), "n": len(rows)},
        "stub": {"spec_sha256": sha256_file(spec_path), "profile": spec["profile"], "driver_sha256": sha256_file(Path(__file__)),
                 "fixtures_sha256": {n: sha256_file(M.closure / "tools/fixtures/pumpswap" / n) for n in ("buy_exact_quote_in_a.json", "global_config.json")},
                 "rpc_calls": dict(sorted(stub.counts.items())), "rebroadcasts_ignored": stub.rebroadcasts},
        "key": {"ephemeral_pubkey": eph, "unpatched": unpatched, "differs_from_both_before_patching": True, "credentials_directory_set": False,
                "secret_scan": leaks},
        "network": {"socket_calls": net.calls, "env_removed_names": removed},
        "patches": [{"module": t.__name__, "attr": a, "value": str(v)} for t, a, v in patches],
        "config": {k2: v for k2, v in cfg.items() if k2 != "intents_file"}, "limits": {k2: (v if not isinstance(v, Path) else str(v)) for k2, v in vars(ex.h5).items()},
        "tier": ex.tier, "total_stop_lamports": ex._total_stop_lamports(), "halts": halts, "open_at_end": [len(opened[0]), len(opened[1])],
        "pick_status_final": dict(Counter(final.values())),
        "result": {"lines": lines.count("\n"), "md5": hashlib.md5(lines.encode()).hexdigest(), "sha256": hashlib.sha256(lines.encode()).hexdigest(),
                   "accepted": sum(1 for o in out if o[2] == "accepted"), "skipped_reasons": dict(sorted(parse_reason_lines(lines).items()))},
        "timing_audit": audit,
        "input_rewrites": ["SD_slot and state_slot are renumbered onto the stub chain (SD_slot = chain slot of decision_T + 200 ms, the pick's own SD - state gap kept)"],
    }
    reasons_seen = set(parse_reason_lines(lines))
    parse_refusals = sorted(x for x in reasons_seen if x.startswith("bad_pick:") or x in ("pre_window", "unparsed"))
    outside = sorted(reasons_seen - set(parse_refusals) - set(c.STOP_REASONS) - set(c.BOOK_REASONS))  # the executor's own enumerated sets
    meta["reasons"] = {"in_executor_stop_set": sorted(reasons_seen & set(c.STOP_REASONS)), "in_executor_book_set": sorted(reasons_seen & set(c.BOOK_REASONS)),
                       "parse_refusals": parse_refusals, "outside_both_sets": outside}
    problems = []
    if outside:
        problems.append(f"reasons outside the executor's stop and book sets (a harness fault, not a limit): {outside}")
    if audit["separation_violations"]:  # the comparison with the reference would depend on a few hundred ms of scheduling: not a pass
        problems.append("ambiguous timing: " + "; ".join(audit["separation_violations"][:5]))
    if net.calls:
        problems.append(f"{net.calls} socket calls")
    if leaks["hits"]:
        problems.append("the secret was found in the outputs")
    if halts:
        problems.append(f"halts latched: {halts}")
    if opened[0] or opened[1]:
        problems.append("positions left open")
    if set(final.values()) - {"filled", "unfilled"}:
        problems.append(f"picks left pending: {set(final.values())}")
    meta["problems"] = problems
    (work / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return meta


class Driver:
    """Steps the executor's loop (heartbeat, slot polls, prewarm, housekeeping, exit_tick) on the stub clock and hands it the picks."""

    IDLE_LEAD_MS = 150_000

    def __init__(self, M: SimpleNamespace, ex: Any, stub: StubChain, clock: Clock, spec: dict[str, Any]) -> None:
        self.M, self.ex, self.stub, self.clock, self.spec = M, ex, stub, clock, spec
        self.k = spec["constants"]
        self.t_hb = self.t_slot = self.t_pre = -10**18
        self.queue: dict[str, list[int]] = {}
        self.audit: dict[str, Any] = {"close_dev_ms_max": 0, "closes": [], "separation_violations": []}
        self.accepted_idx: list[int] = []

    def _beat(self) -> None:
        now, ex = self.clock(), self.ex
        if now - self.t_hb >= 30_000:
            self.t_hb = now
            ex._on_heartbeat({"type": "c1nf_heartbeat", "model_shas": [self.k["model_sha"]]})
        if now - self.t_slot >= 2_000:
            self.t_slot = now
            ex.refresh_slot()
        if now - self.t_pre >= 10_000:
            self.t_pre = now
            ex.prewarm()
        ex.housekeeping(now)
        ex.exit_tick(now)
        for mint in [m for m in self.queue if self.queue[m] and m not in ex.state.open and m not in ex.state.pending]:
            idx = self.queue[mint].pop(0)
            dev = now - self.spec["picks"][idx]["close_ms"]
            self.audit["closes"].append({"idx": idx, "close_ms": now, "spec_close_ms": self.spec["picks"][idx]["close_ms"], "dev_ms": dev})
            self.audit["close_dev_ms_max"] = max(self.audit["close_dev_ms_max"], abs(dev))

    def advance_to(self, target: int) -> None:
        clock = self.clock
        while clock.t < target:
            busy = bool(self.ex.state.open or self.ex.state.pending)
            if not busy and target - clock.t > self.IDLE_LEAD_MS + 4_000:
                clock.t = target - self.IDLE_LEAD_MS  # idle: skip the wait, then poll the last 150 s as the prewarm loop would
            clock.t = min(target, clock.t + (100 if busy else 2_000))
            self._beat()

    def handle(self, i: int, r: dict[str, Any]) -> tuple[str, int, str, str]:
        M, ex, k = self.M, self.ex, self.k
        sp = self.spec["picks"][i]
        self.advance_to(sp["receipt_ms"])
        t = int(r["decision_T_ms"])
        sd = self.stub.slot_at(t + 200)
        row = {f: r[f] for f in PICK_FIELDS if f in r}
        gap = max(1, min(int(r["SD_slot"]) - int(r["state_slot"]), 50)) if "state_slot" in r and "SD_slot" in r else 1
        row["SD_slot"], row["state_slot"] = sd, sd - gap  # the stub chain's own numbering of the same decision (recorded in the meta)
        pick, bad = M.c.parse_pick(row)
        if pick is None:
            return str(r["mint"]), t, "skipped", bad or "unparsed"
        self._beat()
        ex.handle_pick(pick)
        st = ex.extra.picks.get(pick.pick_id)
        if st is None:
            raise StubError("the executor kept no pick_status row for a pick")
        if st["status"] == "pending":
            if pick.mint not in ex.state.pending:
                raise StubError("pending status without a pending buy")
            self.queue.setdefault(pick.mint, []).append(i)
            self.accepted_idx.append(i)
            return pick.mint, t, "accepted", "filled"
        if st["status"] != "unfilled" or not st["reason"]:
            raise StubError(f"unexpected pick status {st}")
        return pick.mint, t, "skipped", str(st["reason"])

    def drain(self) -> None:
        last = max([p["close_ms"] for p in self.spec["picks"]] + [self.clock()])
        self.advance_to(last + 30_000)
        tol = self.k["timing_tolerance_ms"]
        if self.audit["close_dev_ms_max"] > tol:
            self.audit["separation_violations"].append(f"a close was {self.audit['close_dev_ms_max']} ms from its spec instant (tolerance {tol})")
        picks = self.spec["picks"]
        accepted = [picks[i] for i in self.accepted_idx]
        for p in picks:
            for a in accepted:
                if abs(p["receipt_ms"] - a["close_ms"]) <= tol and p["idx"] != a["idx"]:
                    self.audit["separation_violations"].append(f"pick {p['idx']} is handled within {tol} ms of the close of pick {a['idx']}")
                if p["mint"] == a["mint"] and abs(p["decision_T_ms"] - (a["close_ms"] + 60_000)) <= tol:
                    self.audit["separation_violations"].append(f"pick {p['idx']} is within {tol} ms of the cooldown edge of pick {a['idx']}")
        for a in accepted:
            if abs(a["close_ms"] % DAY_MS) <= tol or abs(a["close_ms"] % DAY_MS - DAY_MS) <= tol:
                self.audit["separation_violations"].append(f"the close of pick {a['idx']} is within {tol} ms of UTC midnight")


# --- commands -------------------------------------------------------------------------------------------------------------------------------------------------
def cmd_extract(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    lo = int(__import__("datetime").datetime.strptime(a.day, "%Y-%m-%d").replace(tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for f in sorted(Path(a.picks_dir).glob("c1nf-*picks-*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if isinstance(r, dict) and r.get("type") == "c1nf_pick" and lo <= int(r["decision_T_ms"]) < lo + DAY_MS:
                key = (str(r["mint"]), int(r["decision_T_ms"]))
                if key in rows:
                    raise SystemExit(f"duplicate pick {key[0]},{key[1]}")
                rows[key] = {f2: r[f2] for f2 in PICK_FIELDS if f2 in r}
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in sorted(rows.values(), key=lambda r: (int(r["decision_T_ms"]), str(r["mint"]))))
    out.write_text(text)
    sha = hashlib.sha256(text.encode()).hexdigest()
    Path(str(out) + ".sha256").write_text(f"{sha}  {out.name}\n")
    print(json.dumps({"day": a.day, "n": len(rows), "sha256": sha, "out": str(out)}))
    return 0


def synth_rows(M: SimpleNamespace, kind: str) -> list[dict[str, Any]]:
    layout = [(t, m) for t, m, _r in synthetic_limits_layout()] if kind == "limits" else realistic_layout()
    stub_pool = json.loads((M.closure / "tools/fixtures/pumpswap/buy_exact_quote_in_a.json").read_text())["pool_account_b64"]
    V = M.tx.parse_pool_account(base64.b64decode(stub_pool))["virtual_quote_reserves"]
    rows = []
    for j, (t, mint) in enumerate(layout):
        pool = str(M.tx.canonical_pool(M.Pubkey.from_string(mint)))
        sd = 400_000_000 + j * 1000
        rows.append({"type": "c1nf_pick", "mint": mint, "pool": pool, "decision_T_ms": t, "SD_slot": sd, "pred": 0.05, "h_top1": 0.1, "stage1": True,
                     "feature_hash": hashlib.sha256(f"{mint}:{t}".encode()).hexdigest()[:16], "model_sha": CANARY_MODEL_SHA, "q_lamports": 20_000_000_000 + V,
                     "base_reserve": 400_000_000 * 10**6, "v_lamports": V, "state_slot": sd - 1, "t_ms": t + 200})
    return rows


def cmd_synth(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    M = load_closure(Path(a.closure_dir))
    rows = synth_rows(M, a.kind)
    out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    print(json.dumps({"kind": a.kind, "n": len(rows), "sha256": sha256_file(out), "out": str(out)}))
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    meta = run_once(Path(a.closure_dir), Path(a.picks), Path(a.spec), Path(a.work_dir), a.end_ms)
    print(json.dumps({"md5": meta["result"]["md5"], "lines": meta["result"]["lines"], "problems": meta["problems"]}))
    return 1 if meta["problems"] else 0


def cmd_prove(a: argparse.Namespace) -> int:
    work = Path(a.work_dir)
    if work.exists():
        raise SystemExit(f"refused: {work} exists")
    work.mkdir(parents=True)
    picks = Path(a.picks)
    if a.end_ms is None and a.profile == PROFILE_SYNTH:
        a.end_ms = SYNTH_LIMITS_END_MS
    spec = build_stub_spec(read_pick_rows(picks), a.profile)
    spec_path = work / "spec.json"
    spec_path.write_text(canon(spec))
    py = [sys.executable, "-I", str(Path(__file__).resolve())]
    extra = ["--end-ms", str(a.end_ms)] if a.end_ms is not None else []
    res = []
    for n in (1, 2):
        r = subprocess.run([*py, "run", "--closure-dir", a.closure_dir, "--picks", str(picks), "--spec", str(spec_path), "--work-dir", str(work / f"exec{n}"), *extra],
                           capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL)
        res.append(r)
        if r.returncode != 0:
            sys.stderr.write(r.stdout[-2000:] + r.stderr[-2000:])
            return 1
    ref_py = [sys.executable, "-I", str(Path(__file__).resolve().parent / "c1nf_item14c_reference.py")]
    r3 = subprocess.run([*ref_py, "--picks", str(picks), "--spec", str(spec_path), "--out", str(work / "reference.csv"), *extra], capture_output=True, text=True,
                        timeout=600, stdin=subprocess.DEVNULL)
    if r3.returncode != 0:
        sys.stderr.write(r3.stdout[-2000:] + r3.stderr[-2000:])
        return 1
    e1, e2, ref = ((work / n).read_bytes() for n in ("exec1/lines.csv", "exec2/lines.csv", "reference.csv"))
    m1 = json.loads((work / "exec1/meta.json").read_text())
    reasons = m1["result"]["skipped_reasons"]
    need = [x for x in (a.require_reasons or "").split(",") if x]
    missing = [x for x in need if x not in reasons]
    proof = {"schema": "c1nf_item14c_proof_v1", "profile": a.profile, "picks_sha256": sha256_file(picks), "spec_sha256": sha256_file(spec_path),
             "n_picks": len(spec["picks"]), "executor_lines": e1.count(b"\n"), "executor_md5": hashlib.md5(e1).hexdigest(), "executor_rerun_md5": hashlib.md5(e2).hexdigest(),
             "reference_md5": hashlib.md5(ref).hexdigest(), "md5_equal": e1 == ref, "rerun_byte_equal": e1 == e2, "line_count_equals_pick_count": e1.count(b"\n") == len(spec["picks"]),
             "accepted": m1["result"]["accepted"], "skipped_reasons": reasons, "required_reasons": need, "required_reasons_missing": missing,
             "executor_meta_sha256": [sha256_file(work / f"exec{n}/meta.json") for n in (1, 2)], "end_ms": a.end_ms}
    proof["pass"] = bool(proof["md5_equal"] and proof["rerun_byte_equal"] and proof["line_count_equals_pick_count"] and not missing)
    (work / "proof.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    print(json.dumps(proof, sort_keys=True))
    return 0 if proof["pass"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--picks-dir", required=True)
    p.add_argument("--day", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("synth")
    p.add_argument("--closure-dir", required=True)
    p.add_argument("--kind", choices=("limits", "realistic"), required=True)
    p.add_argument("--out", required=True)
    for name in ("run", "prove"):
        p = sub.add_parser(name)
        p.add_argument("--closure-dir", required=True)
        p.add_argument("--picks", required=True)
        p.add_argument("--work-dir", required=True)
        p.add_argument("--end-ms", type=int, default=None)
        if name == "run":
            p.add_argument("--spec", required=True)
        else:
            p.add_argument("--profile", choices=PROFILES, default=PROFILE_LOSS_FIRST)
            p.add_argument("--require-reasons", default="")
    a = ap.parse_args(argv)
    return {"extract": cmd_extract, "synth": cmd_synth, "run": cmd_run, "prove": cmd_prove}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
