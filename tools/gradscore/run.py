"""gradscore collector loop. PAPER ONLY: public price APIs + Anthropic Haiku calls; no wallet, no trade, no signature.

  python -m tools.gradscore.run --out DIR --config FILE [--duration SECONDS] [--no-haiku] [--discover-only]

Files under DIR (daily-rotated by UTC day of the row): discoveries-DATE.jsonl, cards-DATE.jsonl, scores-DATE.jsonl, state.json,
haiku/gradscore-haiku-ledger-DATE.jsonl. Outcomes are NOT written here (see tools.gradscore.outcomes).
The Anthropic key comes from the environment (scripts/ops/gradscore.sh loads /var/lib/mal/anthropic/anthropic.env like slowbot-paper.sh).
Opens no tape, shadow, ledger, block or book output.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tools.gradscore import card as cd, config as cfgmod, discover as dv, prompt as pr, score as sc
from tools.slowbot.positions import atomic_write_json
from tools.slowbot.sources import DexScreener, Gecko, HttpError, TokenBucket, urllib_transport


def iso(now_s):
    return datetime.fromtimestamp(now_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(now_s):
    return datetime.fromtimestamp(now_s, timezone.utc).strftime("%Y-%m-%d")


def day_file(out, kind, now_s):
    return os.path.join(out, f"{kind}-{day_of(now_s)}.jsonl")


class Collector:
    def __init__(self, out_dir, cfg, *, disc, caller=None, xfeed=None, clock=time.time, sleep=time.sleep, ipfs_fetch=None):
        self.out, self.cfg, self.disc, self.caller, self.xfeed = out_dir, cfg, disc, caller, xfeed
        self.clock, self.sleep, self.ipfs_fetch = clock, sleep, ipfs_fetch
        self.stop = False
        self.cap_day = None
        self.state_path = os.path.join(out_dir, "state.json")
        self.state = self._load_state()
        self.counters = {"polls": 0, "poll_errors": 0, "discovered": 0, "not_pump": 0, "scored": 0, "skipped": 0, "cap_skips": 0}

    # -- state / resume
    def _load_state(self):
        try:
            with open(self.state_path) as f:
                st = json.load(f)
        except Exception:
            st = {}
        seen = st.get("seen") if isinstance(st.get("seen"), dict) else {}
        for p in glob.glob(os.path.join(self.out, "discoveries-*.jsonl")):  # rebuild from the logs if state is lost
            try:
                for line in open(p):
                    try:
                        r = json.loads(line)
                        seen.setdefault(r["mint"], float(r["first_seen_s"]))
                    except (ValueError, KeyError, TypeError):
                        continue
            except OSError:
                continue
        return {"seen": seen}

    def _save_state(self, now_s):
        keep = self.cfg["seen_keep_s"]
        self.state["seen"] = {m: t for m, t in self.state["seen"].items() if now_s - t <= keep}
        atomic_write_json(self.state_path, self.state)

    def _append(self, kind, now_s, row):
        sc.append_jsonl(day_file(self.out, kind, now_s), row)

    # -- one poll
    def poll_once(self):
        now = self.clock()
        self.counters["polls"] += 1
        try:
            pools = self.disc.new_pools(now, self.cfg["max_pool_age_min"], self.cfg.get("new_pools_pages", 1))
        except (HttpError, OSError, ValueError):
            self.counters["poll_errors"] += 1
            return 0
        new, in_poll = [], set()
        for p in pools:  # dedupe by mint within the poll too (two new pools of one mint in a page = one handling)
            if p["mint"] not in self.state["seen"] and p["mint"] not in in_poll:
                in_poll.add(p["mint"])
                new.append(p)
        for p in sorted(new, key=lambda r: r["pool_created_at"] or ""):
            if self.stop:
                break
            self._handle(p)
        self._save_state(self.clock())
        return len(new)

    def _handle(self, p):
        now = self.clock()
        row = dict(p, ts_utc=iso(now), first_seen_s=now, source="geckoterminal_new_pools")
        self._append("discoveries", now, row)  # logged first, even if everything after fails
        self.state["seen"][p["mint"]] = now
        self.counters["discovered"] += 1
        base = {"mint": p["mint"], "pool": p["pool"], "ts_utc": iso(now), "first_seen_s": now}
        try:
            info = self.disc.token_info(p["mint"])
            pair = self.disc.dex_pair(p["mint"], p["pool"])
            meta = dv.fetch_ipfs_metadata(info.get("metadata_uri"), self.cfg["ipfs_gateway"], self.cfg["ipfs_timeout_s"], self.ipfs_fetch) if info.get("metadata_uri") else None
            xc = None
            if self.xfeed is not None:
                try:
                    xc = self.xfeed.snippets(p["mint"], p.get("symbol"))
                except Exception:
                    xc = None
            card = cd.build_card(p, info, pair, meta, xc)
            text, sha = cd.card_text(card), cd.card_sha256(card)
            self._append("cards", now, dict(base, card_text=text, card_sha256=sha, graduated=info.get("launchpad_completed"),
                                            has_x_context="x_context" in card, has_metadata_uri=bool(info.get("metadata_uri"))))
        except Exception as e:  # enrichment failed: discovery row stays, scores say why
            for v in self.cfg["variants"]:
                self._append("scores", now, dict(base, variant=v, action="SKIP", skip_reason=f"card_{type(e).__name__}"))
            self.counters["skipped"] += len(self.cfg["variants"])
            return
        eligible = p["is_pump_suffix"] or info.get("launchpad_completed") is True
        for v in self.cfg["variants"]:
            r = self._score(v, text, eligible)
            self._append("scores", self.clock(), dict(base, card_sha256=sha, **r))

    def _score(self, v, text, eligible):
        blank = {"variant": v, "action": "SKIP", "skip_reason": None, "cost_usd": 0.0, "usage": None}
        if not eligible:
            self.counters["not_pump"] += 1
            return dict(blank, skip_reason="not_pump_graduate")
        if self.caller is None:
            self.counters["skipped"] += 1
            return dict(blank, skip_reason="no_caller")
        today = day_of(self.clock())
        if self.cap_day == today:
            self.counters["cap_skips"] += 1
            return dict(blank, skip_reason="budget_cap")
        r = sc.score_one(self.caller, v, text, self.cfg["max_tokens"])
        if r.pop("cap"):
            self.cap_day = today
            self.counters["cap_skips"] += 1
        elif r["action"] == "SCORED":
            self.counters["scored"] += 1
        else:
            self.counters["skipped"] += 1
        return r

    def run(self, duration=None):
        t0 = self.clock()
        while not self.stop and (duration is None or self.clock() - t0 < duration):
            self.poll_once()
            if self.stop:
                break
            self.sleep(self.cfg["poll_s"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--no-haiku", action="store_true", help="discovery and cards only")
    ap.add_argument("--discover-only", action="store_true", help="alias of --no-haiku, and skips the X feed")
    a = ap.parse_args(argv)
    cfg = cfgmod.load_config(a.config)
    tr = urllib_transport
    limits = {"ds_pairs": cfg["ds_pairs_per_min"], "ds_profiles": 55, "gecko": cfg["gecko_per_min"]}
    gt = Gecko(tr, limits=limits)
    ds = DexScreener(tr, limits=limits)
    disc = dv.Discovery(gt.bucket, ds, tr, time.sleep)
    caller = None
    if not (a.no_haiku or a.discover_only) and os.environ.get("ANTHROPIC_API_KEY"):
        caller = sc.DailyCaller(os.path.join(a.out, "haiku"), cfg["daily_usd_cap"])
    xfeed = None
    if not a.discover_only and cfg.get("x_feed") and os.path.isdir(cfg["x_feed"]):
        from tools.slowbot.runner import XFeed
        xfeed = XFeed(cfg["x_feed"])
    col = Collector(a.out, cfg, disc=disc, caller=caller, xfeed=xfeed)
    signal.signal(signal.SIGTERM, lambda *_: setattr(col, "stop", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(col, "stop", True))
    col.run(duration=a.duration)
    print("gradscore done", " ".join(f"{k}={v}" for k, v in col.counters.items()), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
