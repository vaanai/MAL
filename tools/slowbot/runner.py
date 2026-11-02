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
`missed_restart` and not opened. Daily report at 00:05Z: reports/arms-DAY.{json,txt} and reports/console-slowbot.json, with the
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

from tools.slowbot import decide as dc, prescreen as ps, report as rp, scan, universe as uv
from tools.slowbot.positions import ATA_RENT_LAMPORTS, POLL_S, PositionManager, atomic_write_json
from tools.slowbot.quotes import WSOL, Jupiter
from tools.slowbot.sources import DexScreener, Gecko

SCAN_PERIOD_S = 300.0
REPORT_AFTER = (0, 5)  # 00:05Z
STALE_S = 120.0
PROCESSED_KEEP_S = 3 * 86400
HEARTBEAT_S = 30.0
X_MAX = dc.MAX_X_CONTEXT
COUNTERS = ("scans", "scan_failures", "candidates", "missed_restart", "q2_total", "q2_failed", "q2_late", "h_unavailable",
            "h_calls", "h_buys", "r_draws", "r_skips", "polls", "liq_fetch_failed", "x_context_attached")


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


class XFeed:
    """Optional x_context source. A file (JSONL rows or a JSON list) or an http(s) URL. Rows: a string, or a dict with `text` and
    optionally `mint` / `ticker` / `symbol`. For a URL, `?mint=..&ticker=..` is appended and a JSON list is expected back.
    Any failure yields None (no context). At most 5 snippets."""

    def __init__(self, src, fetch=None):
        self.src = src
        self.is_url = src.startswith(("http://", "https://"))
        self.fetch = fetch or self._http
        self._cache = (None, None)

    @staticmethod
    def _http(url):
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 (operator-supplied feed)
            return json.loads(r.read(1_000_000).decode("utf-8"))

    def _file_rows(self):
        st = os.stat(self.src)
        key = (st.st_mtime_ns, st.st_size)
        if self._cache[0] != key:
            with open(self.src) as f:
                txt = f.read()
            try:
                rows = json.loads(txt)
            except ValueError:
                rows = []
                for line in txt.splitlines():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
            self._cache = (key, rows if isinstance(rows, list) else [])
        return self._cache[1]

    def snippets(self, mint, ticker):
        try:
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
                 progress_path=None, helius_post=None):
        self.out = str(out_dir)
        os.makedirs(self.out, exist_ok=True)
        self.ds, self.gt, self.jup = ds, gt, jup
        self.caller, self.haiku_on, self.x_feed = caller, haiku_on, x_feed
        self.clock, self.sleep, self.period, self.poll = clock, sleep, period, poll
        self.seed = ps.PRESCREEN["arm_seed"] if seed is None else seed
        self.top_holders, self.log, self.requote_delays, self.helius_post = top_holders, log, requote_delays, helius_post
        self.progress_path = progress_path
        self.stop = False
        self.decisions_path = os.path.join(self.out, "decisions.jsonl")
        self.state_path = os.path.join(self.out, "runner-state.json")
        self.rs = self._load_state()
        self._fills = {}   # mint -> entry quote dict or None, for the candidate being opened
        self._liq = {}     # mint -> liq_usd from the latest DexScreener batch
        self._last_hb = 0.0
        self.pm = PositionManager(os.path.join(self.out, "positions-state.json"), os.path.join(self.out, "positions.jsonl"),
                                  self._sell_quote, buy_quote_fn=self._buy_quote, now_fn=clock)
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

    # -- one poll
    def poll_once(self):
        self._refresh_liq()
        self.pm.tick()
        self._bump("polls")
        self._save()

    # -- one scan + decisions + entries
    def scan_cycle(self):
        t_scan = self.clock()
        try:
            counts = scan.scan_once(self.ds, self.gt, self.jup, self.out, top_holders=self.top_holders,
                                    requote_delays=self.requote_delays, sleep=self.sleep, clock=self.clock,
                                    helius_post=self.helius_post, log=self.log)
        except Exception as e:
            self._bump("scan_failures")
            self.log("scan failed:", type(e).__name__)
            self._save()
            return None
        self._bump("scans")
        self.process_new_candidates()
        self._prune_processed()
        self._save()
        return counts

    def _prune_processed(self):
        now = self.clock()
        pr = self.rs["processed"]
        for k in [k for k, v in pr.items() if now - v > PROCESSED_KEEP_S]:
            del pr[k]

    def process_new_candidates(self):
        now = self.clock()
        pr = self.rs["processed"]
        days = []
        for back in (1, 0):  # yesterday first, so a restart across midnight keeps order
            ts = ps.utc_iso(dt.datetime.fromtimestamp(now - back * 86400, dt.timezone.utc))
            days.append(ts)
        for ts_day in days:
            cands = _read_jsonl(ps.day_file(self.out, "candidates", ts_day))
            if not cands:
                continue
            quotes = _read_jsonl(ps.day_file(self.out, "quotes", ts_day))
            for c in cands:
                cid = f"{c.get('mint')}:{c.get('ts_utc')}"
                if cid in pr or not c.get("mint") or not c.get("ts_utc"):
                    continue
                pr[cid] = now
                self._save()  # at most once: a crash after this point never double-opens or double-calls the model
                try:
                    age = now - _epoch(c["ts_utc"])
                except ValueError:
                    age = 1e9
                if age > STALE_S:
                    self._bump("missed_restart")
                    self._append_decision({"kind": "missed_restart", "ts_utc": c["ts_utc"], "mint": c["mint"], "age_s": age})
                    continue
                self._handle_candidate(dict(c, candidate_id=cid), quotes)

    def _entry_from_quotes(self, c, quotes):
        """The scanner's paper fill for this candidate (rows of the same scan: same ts_utc and mint). Counts the +2 s quote."""
        mint, ts = c["mint"], c["ts_utc"]
        rows = [q for q in quotes if q.get("mint") == mint and q.get("ts_utc") == ts]
        q2 = next((q for q in rows if q.get("kind") == "quote" and q.get("delay_s") == 2), None)
        fill = next((q for q in reversed(rows) if q.get("kind") == "paper_fill"), None)
        self._bump("q2_total")
        if q2 is None or not q2.get("ok"):
            self._bump("q2_failed")
        elif not q2.get("offset_ok", True):
            self._bump("q2_late")
        if fill is None or fill.get("failed_entry"):
            return None
        return {"in_lamports": fill.get("in_lamports"), "out_lamports": fill.get("token_out"), "liq_usd": None}

    def _handle_candidate(self, c, quotes):
        self._bump("candidates")
        self._fills = {c["mint"]: self._entry_from_quotes(c, quotes)}
        ts = ps.utc_iso(dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc))
        cid = c["candidate_id"]
        # R draw first, at H's trailing skip rate over the decisions logged before this candidate
        rate = dc.trailing_skip_rate(_read_jsonl(self.decisions_path))
        skipped = dc.r_skip(self.seed, cid, rate)
        self._append_decision({"kind": "r_draw", "ts_utc": ts, "mint": c["mint"], "candidate_id": cid, "seed": self.seed,
                               "skip_rate": rate, "skipped": skipped})
        self._bump("r_draws")
        self._bump("r_skips", 1 if skipped else 0)
        arms = ["T"]
        # H
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
            row = dc.decide(dict(c, arm="H"), self.caller, x_context=xc, log_path=self.decisions_path)
            self._bump("h_calls")
            if row["h_buy"]:
                self._bump("h_buys")
                arms.append("H")
        if not skipped:
            arms.append("R")
        c["arms"] = [a for a in ("T", "H", "R") if a in arms]
        self.pm.open_all(c)

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
            "report_day": day, "paper_only": True, "live_path": False, "report": rep,
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
        next_scan = t0
        next_poll = t0 + self.poll
        self.log(f"runner start restored_admitted={self.restored} haiku={'on' if (self.haiku_on and self.caller) else 'off'}")
        while not self.stop:
            now = self.clock()
            if duration is not None and now - t0 >= duration:
                break
            if now >= next_scan:
                self.scan_cycle()
                next_scan = max(next_scan + self.period, self.clock())
                next_poll = self.clock()  # poll right after a scan that blocked it
            now = self.clock()
            if now >= next_poll:
                self.poll_once()
                next_poll = self.clock() + self.poll
            self.maybe_report()
            pct = 50 if duration is None else min(99, int(100 * (self.clock() - t0) / duration))
            self.heartbeat(pct)
            self.sleep(min(1.0, max(0.0, min(next_scan, next_poll) - self.clock())) or 0.1)
        self._save()
        self.heartbeat_final()

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
    ap.add_argument("--x-feed", default=None, help="file or URL of X snippets; absent = no x_context")
    ap.add_argument("--top-holders", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)

    from tools.slowbot.sources import urllib_transport as tr
    ds, gt, jup = DexScreener(tr), Gecko(tr), Jupiter(tr)
    caller = None
    if not a.no_haiku and os.environ.get("ANTHROPIC_API_KEY"):
        caller = dc.DailyCaller(os.path.join(a.out, "haiku"))
    runner = Runner(a.out, ds=ds, gt=gt, jup=jup, caller=caller, haiku_on=not a.no_haiku,
                    x_feed=XFeed(a.x_feed) if a.x_feed else None, period=a.period, poll=a.poll, seed=a.seed,
                    top_holders=a.top_holders, progress_path=os.environ.get("MISCUSI_PROGRESS") or None)

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
