"""SLOWB-0 paper runner: scan -> decide -> positions -> report in one loop. PAPER ONLY: no wallet, no key for any chain, no send.

  python -m tools.slowbot.runner --out DIR [--no-haiku] [--x-feed PATH_OR_URL] [--duration SECONDS]

Every 300 s the scanner (tools.slowbot.scan.scan_once) runs; each new candidate is seen by all three arms:
  T  opens on every candidate;
  H  calls decide() (Haiku through tools.haiku_caller, own ledger under DIR/haiku, $2/day) and opens on BUY with conviction >= 3;
  R  opens unless the seeded r_skip draw (at H's trailing model-skip rate) says skip; each draw is a `r_draw` row in decisions.jsonl.
If Haiku is off or its key is absent, H logs a `h_unavailable` row per candidate and opens nothing; T and R go on.
Every 30 s the position manager polls open positions with the Jupiter sell quote (liquidity comes from one DexScreener batch per poll).
Entry fill = the scanner's paper fill (worse of the t0 and +2 s buy quotes). A failed +2 s quote is a refused entry (`no_entry_quote`,
counted); a late one (offset off by more than 1 s) still fills and is counted in runner-state.json counters.
The scan blocks the 30 s poll for the length of its +2 s / +30 s re-quotes (about 35 s); that lag is declared, not modelled.

State, all under DIR: scan files (candidates-, checks-, quotes-, rejects-, scans-; state.json), positions.jsonl + positions-state.json,
decisions.jsonl, haiku/ ledger, runner-state.json (processed candidates, counters, last report day), reports/. Resume: a restart
reloads all of it; the 24 h mint dedupe is rebuilt from today's and yesterday's candidates-*.jsonl first (the scanner saves its
`admitted` map only at the end of a scan); a candidate written before a crash but not processed, and older than STALE_S, is logged
`missed_restart` and not opened (a scan's own candidates are never stale however long the scan took).
Entry: one fresh round-trip quote after the Haiku call returns, worse-of with the scanner fill, shared by T/H/R; its real age is
logged on the open row and refused above 5 s. The decision is persisted in runner-state `pending` before the open: retried after a
restart within STALE_S, else `h_decision_orphan`. Paper per-arm cap is 25; arm_full hits are logged and counted (`arm_full_hits`).
Every loop stage is guarded: an exception is logged by class, counted as errors_<stage>, and the loop goes on. A report missed
while the runner was down is not backfilled (the next is cumulative, labelled as_of_utc). MISCUSI_RESUME is informational: the
state dir is the checkpoint. Daily report at 00:05Z: reports/arms-DAY.{json,txt} and reports/console-slowbot.json, with the
net both with the ATA rent refunded (the bot closes the account after the sell) and not refunded. Written-off positions
(rug, no quote) are counted as not refunded.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import sys
import time
import urllib.parse
import urllib.request

from tools.slowbot import decide as dc, intents as itn, prescreen as ps, report as rp, scan, universe as uv
from tools.slowbot.positions import ATA_RENT_LAMPORTS, POLL_S, PositionManager, atomic_write_json
from tools.slowbot.quotes import WSOL, Jupiter
from tools.slowbot.sources import DexScreener, Gecko

SCAN_PERIOD_S = 300.0
REPORT_AFTER = (0, 5)  # 00:05Z
STALE_S = 120.0
PAPER_MAX_PER_ARM = 25  # paper only: every arm trades every candidate it selects, so arms stay matched (a live cap is separate)
PROCESSED_KEEP_S = 3 * 86400
HEARTBEAT_S = 30.0
X_MAX = dc.MAX_X_CONTEXT
COUNTERS = ("scans", "scan_failures", "candidates", "missed_restart", "q2_total", "q2_failed", "q2_late", "h_unavailable",
            "h_calls", "h_buys", "h_budget_cap", "h_caller_error", "h_decision_orphan", "pending_retried",
            "no_fill_row", "post_decision_quote_failed", "arm_full_hits", "r_draws", "r_skips", "polls", "liq_fetch_failed", "x_context_attached", "x_lookup_requested")


def _epoch(ts: str) -> float:
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def _read_jsonl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue  # torn last line after a crash
                if isinstance(r, dict):
                    out.append(r)
    except OSError:
        pass
    return out


def rebuild_admitted(out_dir, now_s) -> int:
    """Rebuild the scanner's 24 h mint dedupe from today's and yesterday's candidates files. Returns the number of mints restored."""
    state = scan.load_state(out_dir)
    adm = state.setdefault("admitted", {})
    n = 0
    for back in (0, 1):
        ts = ps.utc_iso(dt.datetime.fromtimestamp(now_s - back * 86400, dt.timezone.utc))
        for r in _read_jsonl(ps.day_file(out_dir, "candidates", ts)):
            try:
                t = _epoch(r["ts_utc"])
                m = r["mint"]
            except (KeyError, ValueError, TypeError):
                continue
            if now_s - t < ps.PRESCREEN["dedupe_s"] and t > adm.get(m, -1e18):
                adm[m] = t
                n += 1
    if n:
        scan.save_state(out_dir, state)
    return n


X_FEED_STALE_S = 30 * 60
X_LOOKUP_STALE_S = 120 * 60
X_LOOKUP_PER_HOUR = 6
X_SNIPPETS_WITH_SUMMARY = 4
X_LOOKUP_FILES_KEEP = 24


def _norm_ticker(t):
    return str(t or "").strip().lstrip("$").strip().lower()


def _parse_ts(v):
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def helm_summary(r):
    """One summary line for a Helm feed row (dict with `snippets`)."""
    kol = r.get("kol_mentions")
    kol = len(kol) if isinstance(kol, (list, tuple)) else (kol or 0)
    flags = r.get("flags")
    flags = ",".join(str(f) for f in flags) if isinstance(flags, (list, tuple)) and flags else "none"
    return (f"X 60m: mentions={r.get('mentions_60m', 0)} authors={r.get('unique_authors_60m', 0)} "
            f"top_followers={r.get('top_author_followers', 0)} kol={kol} sentiment={r.get('sentiment') or 'none'} flags={flags} "
            "(counts are lower bounds; shill/signal-bot = paid promo, treat as noise or negative)")


class XFeed:
    """Optional x_context source: a file (JSONL rows or a JSON list), an http(s) URL, or a DIRECTORY (Helm's /data/mal/x-feed).
    File rows: a string, or a dict with `text` and optionally `mint` / `ticker` / `symbol`. For a URL, `?mint=..&ticker=..` is
    appended and a JSON list is expected back. Directory: newest x-feed-*.jsonl (else latest.jsonl) plus x-lookup-*.jsonl; rows older
    than 30 min (120 min for lookups) by ts_utc are ignored. Match = exact mint, else ticker ($ stripped, case-insensitive) only
    when the row's mint is null; mint matches win. Helm rows (with `snippets`) give one summary string then up to 4 snippets.
    Any failure yields None (no context). At most 5 snippets in all. With lookups=True (opt-in, default off) a directory source
    also takes lookup requests (request_lookup; append times persisted in state_path): one mint per line in lookup-requests.txt, no duplicates, at most 6 appends per hour, never raises."""

    def __init__(self, src, fetch=None, clock=time.time, lookups=False, state_path=None):
        self.src = src
        self.lookups, self.state_path = lookups, state_path
        self.is_url = src.startswith(("http://", "https://"))
        self.is_dir = (not self.is_url) and os.path.isdir(src)
        self.fetch = fetch or self._http
        self.clock = clock
        self._cache = (None, None)
        self._dcache = {}
        self._req_seen = set()
        self._req_times = self._load_times()

    def _load_times(self):
        try:
            with open(self.state_path) as f:
                return [float(x) for x in json.load(f)]
        except Exception:
            return []

    def _save_times(self):
        if not self.state_path:
            return
        try:
            atomic_write_json(self.state_path, self._req_times)
        except Exception:
            pass

    @staticmethod
    def _http(url):
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 (operator-supplied feed)
            return json.loads(r.read(1_000_000).decode("utf-8"))

    @staticmethod
    def _parse(txt):
        try:
            rows = json.loads(txt)
        except ValueError:
            rows = []
            for line in txt.splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        if isinstance(rows, dict):  # a file holding exactly one JSON object
            rows = [rows]
        return rows if isinstance(rows, list) else []

    def _file_rows(self):
        st = os.stat(self.src)
        key = (st.st_mtime_ns, st.st_size)
        if self._cache[0] != key:
            with open(self.src) as f:
                txt = f.read()
            self._cache = (key, self._parse(txt))
        return self._cache[1]

    def _read_cached(self, path):
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
        hit = self._dcache.get(path)
        if hit is None or hit[0] != key:
            with open(path) as f:
                hit = (key, self._parse(f.read()))
            self._dcache[path] = hit
        return hit[1]

    def _dir_rows(self):
        """Fresh rows from the directory: [(ts, row)], newest first."""
        now = self.clock()
        listing = os.listdir(self.src)
        names = sorted(n for n in listing if n.startswith("x-feed-") and n.endswith(".jsonl"))
        if names:
            feeds = [(names[-1], X_FEED_STALE_S)]
        else:
            feeds = [("latest.jsonl", X_FEED_STALE_S)] if "latest.jsonl" in listing else []
        looks = sorted(n for n in listing if n.startswith("x-lookup-") and n.endswith(".jsonl"))[-X_LOOKUP_FILES_KEEP:]
        out = []
        for name, limit in feeds + [(n, X_LOOKUP_STALE_S) for n in looks]:
            try:
                rows = self._read_cached(os.path.join(self.src, name))
            except Exception:
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                ts = _parse_ts(r.get("ts_utc")) if r.get("ts_utc") else None
                if ts is None:
                    if "snippets" in r:
                        continue  # a Helm row with no readable time cannot be judged fresh
                    ts = now
                if now - ts > limit:
                    continue
                out.append((ts, r))
        out.sort(key=lambda x: -x[0])
        return out

    @staticmethod
    def _row_texts(r):
        if isinstance(r.get("snippets"), (list, tuple)):
            return [s.strip() for s in r["snippets"] if isinstance(s, str) and s.strip()]
        t = r.get("text")
        return [t.strip()] if isinstance(t, str) and t.strip() else []

    def _dir_snippets(self, mint, ticker):
        rows = [r for _, r in self._dir_rows()]
        t = _norm_ticker(ticker)
        hits = [r for r in rows if mint and r.get("mint") == mint]
        if not hits and t:
            hits = [r for r in rows if not r.get("mint") and _norm_ticker(r.get("ticker") or r.get("symbol")) == t]
        out = []
        helm = [r for r in hits if "snippets" in r]
        if helm:
            out.append(helm_summary(helm[0]))
        cap = X_SNIPPETS_WITH_SUMMARY if helm else X_MAX
        seen, texts = set(), []
        for r in hits:
            for s in self._row_texts(r):
                if s not in seen:
                    seen.add(s)
                    texts.append(s)
        return dc.clean_x_context(out + texts[:cap])

    def snippets(self, mint, ticker):
        try:
            if self.is_dir:
                return self._dir_snippets(mint, ticker)
            if self.is_url:
                q = urllib.parse.urlencode({"mint": mint or "", "ticker": ticker or ""})
                rows = self.fetch(self.src + ("&" if "?" in self.src else "?") + q)
                match_all = True
            else:
                rows, match_all = self._file_rows(), False
            out = []
            t = (ticker or "").lower()
            for r in rows if isinstance(rows, list) else []:
                if isinstance(r, str):
                    ok, text = match_all, r
                elif isinstance(r, dict):
                    text = r.get("text")
                    rt = str(r.get("ticker") or r.get("symbol") or "").lower()
                    ok = match_all or (r.get("mint") == mint) or (bool(t) and rt == t)
                else:
                    continue
                if ok and isinstance(text, str) and text.strip():
                    out.append(text.strip())
            return dc.clean_x_context(out[:X_MAX])
        except Exception:
            return None

    def request_lookup(self, mint):
        """Ask Helm for a lookup of this mint. True only if a new line was appended. Never raises."""
        try:
            if not self.lookups or not self.is_dir or not mint or not isinstance(mint, str):
                return False
            mint = mint.strip()
            if not mint or mint in self._req_seen:
                return False
            path = os.path.join(self.src, "lookup-requests.txt")
            existing, tail_nl = set(), True
            try:
                with open(path, "rb") as f:
                    raw = f.read().decode("utf-8", "replace")
                existing = {ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.lstrip().startswith("#")}
                tail_nl = (not raw) or raw.endswith("\n")
            except FileNotFoundError:
                pass
            if mint in existing:
                self._req_seen.add(mint)
                return False
            now = self.clock()
            self._req_times = [x for x in self._req_times if now - x < 3600]
            if len(self._req_times) >= X_LOOKUP_PER_HOUR:
                return False
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o664)
            try:
                os.write(fd, ((mint if tail_nl else "\n" + mint) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            self._req_seen.add(mint)
            self._req_times.append(now)
            self._save_times()
            return True
        except Exception:
            return False


def with_refund(rep, rows):
    """Add the rent-refunded net per arm. The report's net_sol keeps the ATA rent as spent; the bot closes the account after the sell,
    so the refunded case adds ATA_RENT_LAMPORTS back per exit, except no_quote_writeoff exits (the account holds a dead token)."""
    seen, exits = set(), []
    for r in rows:
        if r.get("kind") == "exit" and r.get("id") not in seen:
            seen.add(r["id"])
            exits.append(r)
    out = {}
    for a in rp.ARMS:
        nets = [r["net_sol"] + (0 if r["reason"] == "no_quote_writeoff" else ATA_RENT_LAMPORTS / 1e9)
                for r in exits if r.get("arm") == a]
        s = rp._stats(nets)
        s["unrefunded_net_sol"] = rep["arms"][a]["net_sol"]
        out[a] = s
    rep["rent_refunded"] = {"assumption": f"ATA rent {ATA_RENT_LAMPORTS} lamports returned per exit by closing the account; "
                                          "not for write-offs; the main arms table is the unrefunded case", "arms": out}
    return rep


def format_refund(rep):
    lines = ["", "rent refunded vs not (net_sol):  arm  trades  refunded  unrefunded  mean_refunded  ex_top3_refunded"]
    for a in rp.ARMS:
        s = rep["rent_refunded"]["arms"][a]
        lines.append(f"{a:<4} {s['trades']:>6}  {rp._f(s['net_sol']):>8}  {rp._f(s['unrefunded_net_sol']):>10}  "
                     f"{rp._f(s['mean_sol']):>13}  {rp._f(s['ex_top3_sol']):>16}")
    return "\n".join(lines)


class Runner:
    def __init__(self, out_dir, *, ds, gt, jup, caller=None, haiku_on=True, x_feed=None, clock=time.time, sleep=time.sleep,
                 period=SCAN_PERIOD_S, poll=POLL_S, seed=None, top_holders=False, log=print, requote_delays=(2, 30),
                 progress_path=None, helius_post=None, live_intents=False):
        self.out = str(out_dir)
        os.makedirs(self.out, exist_ok=True)
        self.ds, self.gt, self.jup = ds, gt, jup
        self.caller, self.haiku_on, self.x_feed = caller, haiku_on, x_feed
        self.clock, self.sleep, self.period, self.poll = clock, sleep, period, poll
        self.seed = ps.PRESCREEN["arm_seed"] if seed is None else seed
        self.top_holders, self.log, self.requote_delays, self.helius_post = top_holders, log, requote_delays, helius_post
        self.progress_path = progress_path
        self.live_intents = bool(live_intents)  # default off: paper files are byte-identical
        self.runner_sha = itn.runner_git_sha() if self.live_intents else None  # only computed when the flag is on
        self.stop = False
        self.decisions_path = os.path.join(self.out, "decisions.jsonl")
        self.state_path = os.path.join(self.out, "runner-state.json")
        self.rs = self._load_state()
        self._fills = {}   # mint -> entry quote dict or None, for the candidate being opened
        self._liq = {}     # mint -> liq_usd from the latest DexScreener batch
        self._last_hb = 0.0
        self.pm = PositionManager(os.path.join(self.out, "positions-state.json"), os.path.join(self.out, "positions.jsonl"),
                                  self._sell_quote, buy_quote_fn=self._buy_quote, now_fn=clock,
                                  max_per_arm=PAPER_MAX_PER_ARM)
        self.restored = rebuild_admitted(self.out, clock())

    # -- state
    def _load_state(self):
        try:
            with open(self.state_path) as f:
                s = json.load(f)
            if not isinstance(s, dict):
                raise ValueError
        except (OSError, ValueError):
            s = {}
        s.setdefault("processed", {})
        s.setdefault("last_report_day", None)
        c = s.setdefault("counters", {})
        for k in COUNTERS:
            c.setdefault(k, 0)
        return s

    def _save(self):
        atomic_write_json(self.state_path, self.rs)

    def _bump(self, k, n=1):
        self.rs["counters"][k] = self.rs["counters"].get(k, 0) + n

    def _append_decision(self, row):
        dc.append_jsonl(self.decisions_path, row)

    # -- quote callbacks for the position manager
    def _sell_quote(self, mint, amount):
        q = self.jup.quote(mint, WSOL, amount)
        return {"out_lamports": int(q["outAmount"]), "liq_usd": self._liq.get(mint)}

    def _buy_quote(self, mint, lamports, arm):
        return self._fills.get(mint)

    def _refresh_liq(self):
        mints = sorted({p["mint"] for p in self.pm.open_positions()})
        self._liq = {}
        if not mints:
            return
        try:
            now_ms = self.clock() * 1000.0
            for p in self.ds.pool_snapshots(mints):
                try:
                    r = uv.normalize(p, now_ms)
                except Exception:
                    continue
                if r["mint"] and isinstance(r["liq_usd"], (int, float)):
                    self._liq[r["mint"]] = max(self._liq.get(r["mint"], 0.0), r["liq_usd"])
        except Exception:
            self._bump("liq_fetch_failed")

    # -- guards
    def _guard(self, name, fn, *args, **kw):
        """Run one loop stage. An exception is logged by class name, counted, and the loop goes on."""
        try:
            return fn(*args, **kw)
        except Exception as e:
            try:
                self._bump("errors_" + name)
                self.log(f"stage {name} failed: {type(e).__name__}")
            except Exception:
                pass
            return None

    # -- one poll
    def poll_once(self):
        self._guard("refresh_liq", self._refresh_liq)
        self._guard("tick", self.pm.tick)
        self._bump("polls")
        self.rs["last_alive"] = self.clock()
        self._guard("save", self._save)

    # -- one scan + decisions + entries
    def scan_cycle(self):
        scan_start = self.clock()
        try:
            counts = scan.scan_once(self.ds, self.gt, self.jup, self.out, top_holders=self.top_holders,
                                    requote_delays=self.requote_delays, sleep=self.sleep, clock=self.clock,
                                    helius_post=self.helius_post, log=self.log)
        except Exception as e:
            self._bump("scan_failures")
            self.log("scan failed:", type(e).__name__)
            self._guard("save", self._save)
            return None
        self._bump("scans")
        self._guard("process_candidates", self.process_new_candidates, scan_start)
        self._guard("prune", self._prune_processed)
        self._guard("save", self._save)
        return counts

    def _prune_processed(self):
        now = self.clock()
        pr = self.rs["processed"]
        for k in [k for k, v in pr.items() if now - v > PROCESSED_KEEP_S]:
            del pr[k]

    def process_new_candidates(self, scan_start=None):
        """scan_start: when the scan that just ran began. A candidate written by it (ts_utc >= scan_start, to the second) belongs to
        this cycle and is never stale however long the scan took; STALE_S applies only to older, unprocessed candidates, which exist
        only after a restart."""
        now = self.clock()
        pr = self.rs["processed"]
        for back in (1, 0):  # yesterday first, so a restart across midnight keeps order
            ts_day = ps.utc_iso(dt.datetime.fromtimestamp(now - back * 86400, dt.timezone.utc))
            cands = _read_jsonl(ps.day_file(self.out, "candidates", ts_day))
            if not cands:
                continue
            quotes = _read_jsonl(ps.day_file(self.out, "quotes", ts_day))
            for c in cands:
                cid = f"{c.get('mint')}:{c.get('ts_utc')}"
                if cid in pr or not c.get("mint") or not c.get("ts_utc"):
                    continue
                pr[cid] = now
                self._guard("save", self._save)  # at most once: a crash after this point never double-opens or double-calls the model
                self._guard("candidate", self._process_one, c, cid, quotes, scan_start, now)

    def _process_one(self, c, cid, quotes, scan_start, now):
        try:
            t_c = _epoch(c["ts_utc"])
        except ValueError:
            t_c = None
        fresh = scan_start is not None and t_c is not None and t_c >= int(scan_start)
        if not fresh and (t_c is None or now - t_c > STALE_S):
            self._bump("missed_restart")
            self._append_decision({"kind": "missed_restart", "ts_utc": c["ts_utc"], "mint": c["mint"],
                                   "age_s": None if t_c is None else now - t_c})
            return
        self._handle_candidate(dict(c, candidate_id=cid), quotes)

    def _scanner_fill(self, c, quotes):
        """The scanner's paper fill for this candidate (rows of the same scan: same ts_utc and mint). Counts the +2 s quote."""
        mint, ts = c["mint"], c["ts_utc"]
        rows = [q for q in quotes if q.get("mint") == mint and q.get("ts_utc") == ts]
        q2 = next((q for q in rows if q.get("kind") == "quote" and q.get("delay_s") == 2), None)
        fill = next((q for q in reversed(rows) if q.get("kind") == "paper_fill"), None)
        self._bump("q2_total")
        if q2 is None and fill is None:
            self._bump("no_fill_row")  # e.g. a crash during the re-quote wait: not a failed quote
        elif q2 is None or not q2.get("ok"):
            self._bump("q2_failed")
        elif not q2.get("offset_ok", True):
            self._bump("q2_late")
        info = {"q2_ok": bool(q2 and q2.get("ok")), "q2_offset_ok": None if q2 is None else q2.get("offset_ok")}
        if fill is None or fill.get("failed_entry"):
            return None, info
        return {"in_lamports": fill.get("in_lamports"), "out_lamports": fill.get("token_out")}, info

    def _entry_from_quotes(self, c, quotes):
        """Scanner fill only (kept for tests)."""
        return self._scanner_fill(c, quotes)[0]

    def _post_decision_entry(self, c, quotes):
        """One entry quote for all arms, taken after the Haiku call returned: the worse (lower token_out) of the scanner's fill and a
        fresh round trip. H pays its decision latency; T and R take the same quote, so the pairing holds."""
        sf, info = self._scanner_fill(c, quotes)
        rt = self.jup.round_trip(c["mint"], now=self.clock)
        row = {"kind": "entry_quote", "ts_utc": ps.utc_iso(dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)),
               "mint": c["mint"], "candidate_id": c["candidate_id"], "scanner_fill": sf, "post_rt_ok": bool(rt.get("ok")),
               "post_rt_ts": rt.get("ts"), "post_rt_token_out": rt.get("token_out"), "fill_from": None, **info}
        entry = None
        if sf is not None and rt.get("ok"):
            use_rt = rt["token_out"] <= sf["out_lamports"]
            entry = {"in_lamports": rt["quote_in_lamports"] if use_rt else sf["in_lamports"],
                     "out_lamports": min(rt["token_out"], sf["out_lamports"]), "liq_usd": None, "quote_ts": rt["ts"]}
            row["fill_from"] = "post_decision" if use_rt else "scanner"
        elif sf is not None:
            self._bump("post_decision_quote_failed")
        row["entry"] = entry
        self._append_decision(row)
        return entry

    def _handle_candidate(self, c, quotes):
        self._bump("candidates")
        ts = ps.utc_iso(dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc))
        cid = c["candidate_id"]
        # R draw first, at H's trailing skip rate over the decisions logged before this candidate
        drows = _read_jsonl(self.decisions_path)
        rate = dc.trailing_skip_rate(drows)
        valid = any(r.get("decision_error") is None and r.get("action") in dc.ACTIONS for r in drows)
        src = "h_trailing" if valid else "default_0.5"
        self.rs["r_rate_source"] = src
        skipped = dc.r_skip(self.seed, cid, rate)
        self._append_decision({"kind": "r_draw", "ts_utc": ts, "mint": c["mint"], "candidate_id": cid, "seed": self.seed,
                               "skip_rate": rate, "skipped": skipped, "rate_source": src})
        self._bump("r_draws")
        self._bump("r_skips", 1 if skipped else 0)
        arms = ["T"]
        if not self.haiku_on or self.caller is None:
            self._bump("h_unavailable")
            self._append_decision({"kind": "h_unavailable", "ts_utc": ts, "mint": c["mint"], "candidate_id": cid,
                                   "reason": "no_haiku" if not self.haiku_on else "no_api_key"})
        else:
            xc = None
            if self.x_feed is not None:
                xc = self.x_feed.snippets(c["mint"], c.get("symbol"))
                if xc:
                    self._bump("x_context_attached")
                if self.x_feed.request_lookup(c["mint"]):
                    self._bump("x_lookup_requested")
            row = dc.decide(dict(c, arm="H"), self.caller, x_context=xc, log_path=self.decisions_path)
            self._bump("h_calls")
            err = row.get("decision_error") or ""
            if err.startswith("caller_"):  # cap hit, missing key, transport: the model never judged this candidate
                budget = "BudgetExceeded" in err
                self._bump("h_unavailable")
                self._bump("h_budget_cap" if budget else "h_caller_error")
                self._append_decision({"kind": "h_unavailable", "ts_utc": ts, "mint": c["mint"], "candidate_id": cid,
                                       "reason": "budget_cap" if budget else "caller_error", "decision_error": err})
            elif row["h_buy"]:
                self._bump("h_buys")
                arms.append("H")
                if self.live_intents:
                    c["h_decision"] = {"ts": self.clock(), "conviction": row.get("conviction"), "prompt_sha256": row.get("prompt_sha256")}
        if not skipped:
            arms.append("R")
        c["arms"] = [a for a in ("T", "H", "R") if a in arms]
        # persist the decision BEFORE the open, so a crash in between is retried or logged, never silent
        self.rs.setdefault("pending", {})[cid] = {"c": c, "t": self.clock()}
        self._save()
        self._open_pending(cid, quotes)

    def _open_pending(self, cid, quotes):
        ent = self.rs.get("pending", {}).get(cid)
        if ent is None:
            return
        c = ent["c"]
        try:
            self._fills = {c["mint"]: self._post_decision_entry(c, quotes)}
            res = self.pm.open_all(c)
            for arm, (pos, why) in res.items():
                if pos is None and why == "arm_full":
                    self._bump("arm_full_hits")
            if self.live_intents and res.get("H", (None,))[0] is not None and c.get("h_decision"):
                hd = c["h_decision"]
                itn.append_intent(self.out, itn.build_intent(c, res["H"][0], decision_ts=hd["ts"], conviction=hd["conviction"], now=self.clock(),
                                                       runner_sha=self.runner_sha, prompt_sha256=hd.get("prompt_sha256")))
                self._bump("live_intents_written")
        finally:
            self.rs["pending"].pop(cid, None)  # a failed open is not retried again; the guard counts the error
            self._save()

    def recover_pending(self):
        """After a restart: a decision persisted but not opened is retried while within STALE_S, else logged h_decision_orphan."""
        now = self.clock()
        for cid, ent in list(self.rs.get("pending", {}).items()):
            c = ent["c"]
            if now - ent["t"] <= STALE_S:
                self._bump("pending_retried")
                try:
                    quotes = _read_jsonl(ps.day_file(self.out, "quotes", c["ts_utc"]))
                except Exception:
                    quotes = []
                self._guard("open_pending", self._open_pending, cid, quotes)
            else:
                self._bump("h_decision_orphan")
                self._append_decision({"kind": "h_decision_orphan", "candidate_id": cid, "mint": c.get("mint"),
                                       "arms": c.get("arms"), "age_s": now - ent["t"]})
            self.rs["pending"].pop(cid, None)
        self._guard("save", self._save)

    # -- reports
    def make_report(self, day):
        rows = rp._rows(os.path.join(self.out, "positions.jsonl"))
        dec = rp._rows(self.decisions_path)
        rep = with_refund(rp.build_report(rows, dec), rows)
        rdir = os.path.join(self.out, "reports")
        os.makedirs(rdir, exist_ok=True)
        text = rp.format_table(rep) + format_refund(rep)
        atomic_write_json(os.path.join(rdir, f"arms-{day}.json"), rep)
        with open(os.path.join(rdir, f"arms-{day}.txt"), "w") as f:
            f.write(text + "\n")
        atomic_write_json(os.path.join(rdir, "console-slowbot.json"), {
            "schema": "console.slowbot.v1", "generated_utc": ps.utc_iso(dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)),
            "report_day": day, "as_of_utc": ps.utc_iso(dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)),
            "r_rate_source": self.rs.get("r_rate_source", "none_yet"), "paper_only": True, "live_path": False, "report": rep,
            "runner_counters": self.rs["counters"], "open_positions": len(self.pm.open_positions()),
            "note": "in-sample paper; no promotion-gate claim"})
        return rep

    def maybe_report(self):
        now = dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)
        day = now.strftime("%Y-%m-%d")
        due = now.replace(hour=REPORT_AFTER[0], minute=REPORT_AFTER[1], second=0, microsecond=0)
        if now >= due and self.rs["last_report_day"] != day:
            self.make_report(day)
            self.rs["last_report_day"] = day
            self._save()
            return True
        return False

    # -- heartbeat
    def heartbeat(self, pct=50):
        if not self.progress_path or self.clock() - self._last_hb < HEARTBEAT_S:
            return
        self._last_hb = self.clock()
        c = self.rs["counters"]
        note = (f"slowbot paper: scans={c['scans']} cand={c['candidates']} open={len(self.pm.open_positions())} "
                f"closed={len(self.pm.state['closed'])} polls={c['polls']} q2_failed={c['q2_failed']} q2_late={c['q2_late']}")
        try:
            with open(self.progress_path, "w") as f:
                f.write(json.dumps({"pct": pct, "note": note}))
        except OSError:
            pass

    # -- loop
    def run(self, duration=None):
        t0 = self.clock()
        last = self.rs.get("last_alive")
        if last is not None:  # a restart: positions were not polled while the runner was down
            self._append_decision({"kind": "restart", "ts_utc": ps.utc_iso(dt.datetime.fromtimestamp(t0, dt.timezone.utc)),
                                   "downtime_s": t0 - last})
        self.rs["last_alive"] = t0
        self._guard("recover_pending", self.recover_pending)
        next_scan = t0
        next_poll = t0 + self.poll
        self.log(f"runner start restored_admitted={self.restored} haiku={'on' if (self.haiku_on and self.caller) else 'off'}")
        while not self.stop:
            now = self.clock()
            if duration is not None and now - t0 >= duration:
                break
            if now >= next_scan:
                self._guard("scan_cycle", self.scan_cycle)
                next_scan = max(next_scan + self.period, self.clock())
                next_poll = self.clock()  # poll right after a scan that blocked it
            if self.clock() >= next_poll:
                self._guard("poll", self.poll_once)
                next_poll = self.clock() + self.poll
            self._guard("report", self.maybe_report)
            pct = 50 if duration is None else min(99, int(100 * (self.clock() - t0) / duration))
            self._guard("heartbeat", self.heartbeat, pct)
            self.sleep(min(1.0, max(0.0, min(next_scan, next_poll) - self.clock())) or 0.1)
        self._guard("save", self._save)
        self._guard("heartbeat", self.heartbeat_final)

    def heartbeat_final(self):
        self._last_hb = 0.0
        self.heartbeat(100)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--period", type=float, default=SCAN_PERIOD_S)
    ap.add_argument("--poll", type=float, default=POLL_S)
    ap.add_argument("--duration", type=float, default=None, help="stop after this many seconds (smoke runs)")
    ap.add_argument("--no-haiku", action="store_true", help="T and R only; H logs h_unavailable")
    ap.add_argument("--x-feed", default=None, help="file, directory (Helm x-feed) or URL of X snippets; absent = no x_context")
    ap.add_argument("--x-lookups", action="store_true", help="opt in: append candidate mints to <x-feed dir>/lookup-requests.txt (default off)")
    ap.add_argument("--top-holders", action="store_true")
    ap.add_argument("--live-intents", action="store_true", help="opt in: append OUT/live-intents.jsonl on each H paper BUY (default off)")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)

    from tools.slowbot.sources import urllib_transport as tr
    ds, gt, jup = DexScreener(tr), Gecko(tr), Jupiter(tr)
    caller = None
    if not a.no_haiku and os.environ.get("ANTHROPIC_API_KEY"):
        caller = dc.DailyCaller(os.path.join(a.out, "haiku"))
    runner = Runner(a.out, ds=ds, gt=gt, jup=jup, caller=caller, haiku_on=not a.no_haiku,
                    x_feed=(XFeed(a.x_feed, lookups=a.x_lookups, state_path=os.path.join(a.out, "x-lookup-times.json")) if a.x_feed else None), period=a.period, poll=a.poll, seed=a.seed,
                    top_holders=a.top_holders, live_intents=a.live_intents, progress_path=os.environ.get("MISCUSI_PROGRESS") or None)

    def _stop(*_):
        runner.stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    runner.run(duration=a.duration)
    c = runner.rs["counters"]
    print("runner done", " ".join(f"{k}={v}" for k, v in c.items()), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
