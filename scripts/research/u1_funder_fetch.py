#!/usr/bin/env python3
"""u1_funder_fetch: first-funder fetch for the U1 forward paper book (QP-RULING-1116-u1-forward-spec section 1). PAPER research tool. Nothing here says any book makes money.

What it does: for each launcher wallet L it finds the first SOL inflow >= 1,000,000 lamports (System Program transfer / transferWithSeed / createAccount, outer or inner) in L's
oldest successful transactions: first hop only. RPC methods used: getSignaturesForAddress and getTransaction ONLY (no Enhanced Transactions API, no getTransactionsForAddress, no
webhooks, no DAS). Stored (and nothing else): Table A launcher -> funder + funding_slot + status; Table B funder -> hub_activity. Run counters in the run record.

Credits are counted in code by PRICES (1 credit per call, per the 2026-11-16 cost report, ruling V3) and the run REFUSES to issue a call that would pass the cap: pilot 100,
backfill 30,000, forward 3,000 per UTC day. The Helius key is read only from the env-file path in $U1_HELIUS_ENV_FILE (default: the path CLAUDE.md names for fast-0) and is never
printed, logged, stored or put in an exception text.

Modes (CLI): pilot (10 launchers with the smallest sha256(address), cap 100), backfill (cap 30,000, checkpoint every 100 launchers, resumable), forward (cap 3,000 per UTC day).
Launcher list file: JSON lines {"launcher": addr, "first_create_slot": int} (a launcher list only: no mint, pool, price or outcome).
Output files are written 0440 under --out; agents read counts, sizes and sha256 only, never the addresses.
"""
import argparse, collections, hashlib, json, os, re, sys, time
from pathlib import Path

PRICES = {"getSignaturesForAddress": 1, "getTransaction": 1}  # credits per call (V3: from the cost report, not verified by the ruling author)
CAPS = {"pilot": 100, "backfill": 30_000, "forward_day": 3_000}
PAGE_LIMIT = 1000
MAX_PAGES = 20
MAX_TX_CALLS = 3
MIN_INFLOW_LAMPORTS = 1_000_000
HUB_WINDOW_S = 3600
MAX_RETRIES = 3
RPS = 10.0
PILOT_N = 10
STATUSES = ("resolved", "no_sol_inflow", "deep", "fetch_error", "fetch_capped", "funding_after_create")
TABLE_A_KEYS = ("launcher", "funder", "funding_slot", "status")
TABLE_B_KEYS = ("funder", "hub_activity")
SYSTEM_PROGRAM = "11111111111111111111111111111111"
DEFAULT_ENV_FILE = "/var/lib/mal/fast-listener/helius.env"  # CLAUDE.md names this path; V4: a job host may differ -> $U1_HELIUS_ENV_FILE
RPC_BASE = "https://mainnet.helius-rpc.com/"


class CapReached(Exception):
    """the next call would pass the hard credit cap: not issued"""


class RpcError(Exception):
    """an RPC failure. Carries only a short code, never a URL, header or key."""

    def __init__(self, code, retryable=False):
        super().__init__("rpc error %s" % (code,))
        self.code, self.retryable = code, retryable


class KeyError_(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ credits
class CreditMeter:
    """counts credits by the table price BEFORE a call and refuses to exceed the cap. forward mode: one meter per UTC day (see DailyMeters)."""

    def __init__(self, cap, prices=None):
        self.cap, self.prices = int(cap), dict(prices or PRICES)
        self.used = 0
        self.calls = collections.Counter()

    def charge(self, method):
        p = self.prices[method]  # an unknown method (anything outside the two) is a KeyError: not allowed
        if self.used + p > self.cap:
            raise CapReached("credit cap %d reached (used %d)" % (self.cap, self.used))
        self.used += p
        self.calls[method] += 1


class DailyMeters:
    def __init__(self, cap_per_day=CAPS["forward_day"]):
        self.cap, self.days = cap_per_day, {}

    def for_day(self, day):
        return self.days.setdefault(day, CreditMeter(self.cap))

    def total_used(self):
        return sum(m.used for m in self.days.values())


# ------------------------------------------------------------------------------------------------------------------------------ key and transport
def load_key(env_path=None):
    """-> the key string, from the file at env_path (default: $U1_HELIUS_ENV_FILE, else DEFAULT_ENV_FILE). Accepts a line KEY=value where KEY contains HELIUS and API (or a URL
    holding api-key=). Never prints. A missing or empty file is an error whose text holds no path content beyond the name."""
    p = env_path or os.environ.get("U1_HELIUS_ENV_FILE") or DEFAULT_ENV_FILE
    try:
        text = Path(p).read_text()
    except OSError:
        raise KeyError_("key file unreadable")
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        v = v.strip().strip("'\"")
        m = re.search(r"api-key=([A-Za-z0-9-]+)", v)
        if m:
            return m.group(1)
        if "HELIUS" in k.upper() and "KEY" in k.upper() and v:
            return v
    raise KeyError_("no key line in key file")


def make_transport(key, timeout=30):
    """-> rpc(method, params) -> result. The URL carries the key; it is built here and never leaves this closure; errors drop it."""
    import urllib.request, urllib.error
    url = RPC_BASE + "?api-key=" + key

    def rpc(method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RpcError(e.code, retryable=(e.code == 429 or e.code >= 500)) from None
        except Exception as e:  # noqa: BLE001 - never include the exception text (it can hold the URL)
            raise RpcError(type(e).__name__, retryable=True) from None
        if isinstance(out, dict) and out.get("error"):
            c = out["error"].get("code") if isinstance(out["error"], dict) else "error"
            raise RpcError(c, retryable=False)
        return out.get("result") if isinstance(out, dict) else None

    return rpc


# ------------------------------------------------------------------------------------------------------------------------------ parsing
def _iter_instructions(tx):
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    for ins in msg.get("instructions") or []:
        yield ins
    for grp in ((tx or {}).get("meta") or {}).get("innerInstructions") or []:
        for ins in grp.get("instructions") or []:
            yield ins


def qualifying_inflow(tx, launcher):
    """-> (source, lamports) of the largest System Program transfer / transferWithSeed / createAccount into `launcher` with lamports >= 1,000,000, outer or inner; else None.
    Token-account closes and program CPIs on program-owned accounts are not System Program instructions to L and do not count."""
    best = None
    for ins in _iter_instructions(tx):
        if not isinstance(ins, dict):
            continue
        if not (ins.get("program") == "system" or ins.get("programId") == SYSTEM_PROGRAM):
            continue
        parsed = ins.get("parsed")
        if not isinstance(parsed, dict):
            continue
        info, typ = parsed.get("info") or {}, parsed.get("type")
        if typ in ("transfer", "transferWithSeed"):
            src, dst = info.get("source"), info.get("destination")
        elif typ == "createAccount":
            src, dst = info.get("source"), info.get("newAccount")
        else:
            continue
        lam = info.get("lamports")
        if dst != launcher or not src or isinstance(lam, bool) or not isinstance(lam, int) or lam < MIN_INFLOW_LAMPORTS:
            continue
        if best is None or lam > best[1]:
            best = (src, lam)
    return best


# ------------------------------------------------------------------------------------------------------------------------------ fetcher
class Fetcher:
    """rpc: callable(method, params) -> result (mocked in tests). meter: CreditMeter, or DailyMeters + day_fn for forward mode. clock/sleep_fn are test seams."""

    def __init__(self, rpc, meter, *, rps=RPS, clock=time.monotonic, sleep_fn=time.sleep, day_fn=None):
        self.rpc, self.meter, self.rps = rpc, meter, rps
        self.clock, self.sleep_fn, self.day_fn = clock, sleep_fn, day_fn
        self._last = None
        self.table_a, self.table_b = {}, {}
        self.by_funder = collections.defaultdict(list)  # funder -> traced launchers resolved to it
        self.counters = collections.Counter()
        self.pages_hist = collections.Counter()
        self.pages_per_launcher, self.tx_per_launcher = {}, {}
        self.t0 = self.clock()

    # -- metering and one call
    def _meter(self):
        return self.meter.for_day(self.day_fn()) if isinstance(self.meter, DailyMeters) else self.meter

    def _call(self, method, params):
        for attempt in range(MAX_RETRIES + 1):
            self._meter().charge(method)  # every request is counted, retries included; raises CapReached before the call
            if self._last is not None:
                wait = 1.0 / self.rps - (self.clock() - self._last)
                if wait > 0:
                    self.sleep_fn(wait)
            self._last = self.clock()
            try:
                return self.rpc(method, params)
            except RpcError as e:
                if e.code == 429:
                    self.counters["n429"] += 1
                if not e.retryable or attempt == MAX_RETRIES:
                    raise
                self.counters["retries"] += 1
                self.sleep_fn(min(2 ** attempt, 8) * 0.5)

    def _sigs(self, addr, before=None):
        opts = {"limit": PAGE_LIMIT, "commitment": "finalized"}
        if before:
            opts["before"] = before
        return self._call("getSignaturesForAddress", [addr, opts]) or []

    # -- one launcher
    def fetch_launcher(self, launcher, first_create_slot):
        """-> Table A row dict (keys TABLE_A_KEYS). Hub test of the funder runs here when due."""
        if launcher in self.table_a:
            return self.table_a[launcher]
        pages, txc = 0, 0
        row = {"launcher": launcher, "funder": None, "funding_slot": None, "status": "fetch_error"}
        funding = None
        try:
            keep, before = collections.deque(maxlen=MAX_TX_CALLS), None
            while True:
                if pages >= MAX_PAGES:
                    row["status"] = "deep"
                    break
                page = self._sigs(launcher, before)
                pages += 1
                for e in page:
                    if isinstance(e, dict) and e.get("err") is None and e.get("signature"):
                        keep.append(e["signature"])
                if len(page) < PAGE_LIMIT:
                    row["status"] = "no_sol_inflow"
                    break
                before = page[-1].get("signature")
            if row["status"] == "no_sol_inflow":
                for sig in reversed(keep):  # oldest successful first
                    tx = self._call("getTransaction", [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
                    txc += 1
                    hit = qualifying_inflow(tx, launcher) if tx else None
                    if hit:
                        slot, bt = (tx or {}).get("slot"), (tx or {}).get("blockTime")
                        if not isinstance(slot, int) or isinstance(slot, bool):
                            row["status"] = "fetch_error"
                        elif slot >= first_create_slot:
                            row["status"] = "funding_after_create"
                        else:
                            row.update(funder=hit[0], funding_slot=slot, status="resolved")
                            funding = (sig, bt)
                        break
        except CapReached:
            row.update(funder=None, funding_slot=None, status="fetch_capped")
            funding = None
        except RpcError:
            row.update(funder=None, funding_slot=None, status="fetch_error")
            funding = None
        self.pages_per_launcher[launcher] = pages
        self.tx_per_launcher[launcher] = txc
        self.pages_hist[pages] += 1
        self.counters["status_" + row["status"]] += 1
        if row["status"] == "resolved":
            self._maybe_hub_test(row["funder"], launcher, funding)
            self.by_funder[row["funder"]].append(launcher)
        self.table_a[launcher] = row
        return row

    def _maybe_hub_test(self, funder, launcher, funding):
        if funder in self.table_b or not self.by_funder.get(funder):
            return  # only once a funder has a second traced launcher
        sig, bt = funding
        try:
            page = self._sigs(funder, before=sig)
            self.counters["hub_tests"] += 1
            old = page[-1].get("blockTime") if page else None
            hub = bool(len(page) == PAGE_LIMIT and isinstance(old, int) and isinstance(bt, int) and old >= bt - HUB_WINDOW_S)
            self.table_b[funder] = hub
        except (CapReached, RpcError):
            self.table_b[funder] = "error"

    # -- bulk
    def run(self, launchers, checkpoint_fn=None, checkpoint_every=100):
        """launchers: iterable of (launcher, first_create_slot). Stops issuing calls at the cap: every launcher not reached is fetch_capped."""
        n = 0
        for launcher, slot in launchers:
            self.fetch_launcher(launcher, slot)
            n += 1
            if checkpoint_fn and n % checkpoint_every == 0:
                checkpoint_fn(self)
        if checkpoint_fn:
            checkpoint_fn(self)

    def credits_used(self):
        return self.meter.total_used() if isinstance(self.meter, DailyMeters) else self.meter.used

    def calls_by_method(self):
        if isinstance(self.meter, DailyMeters):
            c = collections.Counter()
            for m in self.meter.days.values():
                c.update(m.calls)
            return dict(c)
        return dict(self.meter.calls)

    # -- stored rows (exactly the keys of section 1.3)
    def table_a_rows(self):
        return [{k: r[k] for k in TABLE_A_KEYS} for r in self.table_a.values()]

    def table_b_rows(self):
        return [{"funder": f, "hub_activity": v} for f, v in self.table_b.items()]


def write_tables(f, out_dir):
    """Table A and B as JSON lines, 0440, atomically replaced. -> {name: sha256}"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shas = {}
    for name, rows in (("table_a.jsonl", f.table_a_rows()), ("table_b.jsonl", f.table_b_rows())):
        data = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows).encode()
        tmp = out / (name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o440)
        os.replace(tmp, out / name)
        shas[name] = hashlib.sha256(data).hexdigest()
    return shas


def load_tables(out_dir):
    """resume: -> (table_a dict, table_b dict) from files written by write_tables, or ({}, {})"""
    a, b = {}, {}
    pa, pb = Path(out_dir) / "table_a.jsonl", Path(out_dir) / "table_b.jsonl"
    if pa.exists():
        for ln in pa.read_text().splitlines():
            r = json.loads(ln)
            a[r["launcher"]] = r
    if pb.exists():
        for ln in pb.read_text().splitlines():
            r = json.loads(ln)
            b[r["funder"]] = r["hub_activity"]
    return a, b


# ------------------------------------------------------------------------------------------------------------------------------ pilot
def pilot_selection(launchers, n=PILOT_N):
    """deterministic, outcome-free: the n launchers with the smallest sha256(address). launchers: iterable of (addr, first_create_slot) -> list in that order"""
    return sorted(launchers, key=lambda t: hashlib.sha256(t[0].encode()).hexdigest())[:n]


def pilot_record(f, n_w, wall_s, dashboard_delta=None):
    """the section 1.4 record: counts only, no address. -> dict with the pass/fail line"""
    n = len(f.table_a)
    pages = list(f.pages_per_launcher.values()) or [0]
    txs = list(f.tx_per_launcher.values()) or [0]
    credits = f.credits_used()
    mean_c = credits / n if n else 0.0
    status = {s: sum(1 for r in f.table_a.values() if r["status"] == s) for s in STATUSES}
    keys_ok = all(set(r) == set(TABLE_A_KEYS) for r in f.table_a_rows()) and all(set(r) == set(TABLE_B_KEYS) for r in f.table_b_rows())
    projected = n_w * mean_c + 0.5 * n_w
    a = projected <= CAPS["backfill"]
    b = status["resolved"] >= 5
    c = status["fetch_error"] == 0
    d = status["funding_after_create"] == 0
    if dashboard_delta is None:
        e, e_note = True, "dashboard delta not available"
    else:
        lo, hi = sorted((credits, dashboard_delta))
        e, e_note = (lo > 0 and hi <= 2 * lo), "dashboard delta %s vs table %s" % (dashboard_delta, credits)
    verdict = "PASS" if (a and b and c and d and e) else ("FAIL_CAP_ONLY (a)" if (b and c and d and e) else "BLOCKED")
    return {"n_launchers": n, "status_counts": status, "pages_per_launcher_mean": sum(pages) / len(pages), "pages_per_launcher_max": max(pages),
            "get_transaction_per_launcher_mean": sum(txs) / len(txs), "get_transaction_per_launcher_max": max(txs), "hub_tests": f.counters.get("hub_tests", 0),
            "calls_by_method": f.calls_by_method(), "credits_table_total": credits, "credits_table_mean_per_launcher": mean_c, "dashboard_note": e_note,
            "retries": f.counters.get("retries", 0), "n429": f.counters.get("n429", 0), "wall_time_s": wall_s,
            "n_funding_after_create": status["funding_after_create"], "stored_keys_exact": keys_ok, "n_w": n_w, "projected_backfill_credits": projected,
            "pass": {"a_projected_le_30000": a, "b_resolved_ge_5": b, "c_no_fetch_error": c, "d_no_funding_after_create": d, "e_credits_within_2x": e}, "verdict": verdict,
            "provider": "Helius RPC", "methods": ["getSignaturesForAddress", "getTransaction"], "note": "no Enhanced API, no getTransactionsForAddress"}


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def read_launcher_list(path):
    out = []
    for ln in Path(path).read_text().splitlines():
        if ln.strip():
            r = json.loads(ln)
            out.append((r["launcher"], int(r["first_create_slot"])))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("pilot", "backfill", "forward"))
    ap.add_argument("--launchers", required=True, help="JSON lines {launcher, first_create_slot}")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-w", type=int, default=None, help="registry launcher count N_W (pilot projection)")
    ap.add_argument("--env-file", default=None, help="path of the key file (default $U1_HELIUS_ENV_FILE)")
    ap.add_argument("--dashboard-delta", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        key = load_key(a.env_file)
    except KeyError_ as e:
        print("u1_funder_fetch: %s" % e, file=sys.stderr)
        return 2
    allL = read_launcher_list(a.launchers)
    if a.mode == "forward":
        from datetime import datetime, timezone
        meter = DailyMeters()
        f = Fetcher(make_transport(key), meter, day_fn=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    else:
        meter = CreditMeter(CAPS["pilot"] if a.mode == "pilot" else CAPS["backfill"])
        f = Fetcher(make_transport(key), meter)
    todo = pilot_selection(allL) if a.mode == "pilot" else allL
    if a.mode != "pilot":
        f.table_a, f.table_b = load_tables(a.out)
        for L, r in f.table_a.items():
            if r["status"] == "resolved":
                f.by_funder[r["funder"]].append(L)
        todo = [t for t in todo if t[0] not in f.table_a]
    f.run(todo, checkpoint_fn=lambda fx: write_tables(fx, a.out))
    shas = write_tables(f, a.out)
    summary = {"mode": a.mode, "credits_by_table": f.credits_used(), "calls_by_method": f.calls_by_method(), "status_counts": dict((k, v) for k, v in f.counters.items() if k.startswith("status_")),
               "retries": f.counters.get("retries", 0), "n429": f.counters.get("n429", 0), "wall_time_s": round(f.clock() - f.t0, 1), "table_sha256": shas}
    if a.mode == "pilot":
        summary["pilot_record"] = pilot_record(f, a.n_w or len(allL), f.clock() - f.t0, a.dashboard_delta)
    print(json.dumps(summary, sort_keys=True))  # counts and hashes only: no address, no key
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
