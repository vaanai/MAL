"""gradscore config: defaults and loader. PAPER ONLY data collection; nothing here trades or signs."""
from __future__ import annotations

import json

DEFAULTS = {
    "daily_usd_cap": 8.00,        # hard cap through HaikuCaller's per-day ledger
    "max_pool_age_min": 15.0,     # score only pools first seen at pool age <= this
    "poll_s": 60.0,               # new_pools poll period
    "max_tokens": 200,
    "variants": ["A", "B"],
    "gecko_per_min": 18,          # run loop; outcomes tracker uses outcomes_gecko_per_min (shared ~30/min public limit)
    "outcomes_gecko_per_min": 10,
    "ds_pairs_per_min": 120,
    "x_feed": "/data/mal/x-feed",  # directory read via tools.slowbot.runner.XFeed; a missing dir = no x_context
    "ipfs_gateway": "https://ipfs.io/ipfs/",
    "ipfs_timeout_s": 4,
    "seen_keep_s": 3 * 86400,
}


def load_config(path=None) -> dict:
    cfg = dict(DEFAULTS)
    if path:
        with open(path) as f:
            user = json.load(f)
        unknown = set(user) - set(DEFAULTS)
        if unknown:
            raise ValueError("unknown config keys: " + ",".join(sorted(unknown)))
        cfg.update(user)
    cap = cfg["daily_usd_cap"]
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap < 0:
        raise ValueError("daily_usd_cap must be a number >= 0")
    if not cfg["variants"] or not set(cfg["variants"]) <= {"A", "B"}:
        raise ValueError("variants must be a non-empty subset of A,B")
    return cfg
