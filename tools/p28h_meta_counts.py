#!/usr/bin/env python3
"""COUNTS ONLY over /data/mal/meta-capture creates/ and meta/ for 2026-10-17..10-24.
Prints per-day row counts and field fill counts. Never prints a value except
small status enums on a fixed key whitelist; never joins to anything."""
import collections
import json
import os
import sys

ROOT = "/data/mal/meta-capture"
DAYS = ["2026-10-%02d" % d for d in range(17, 25)]
NEST = {"name", "symbol", "description", "image", "twitter", "telegram",
        "website", "showName", "createdOn"}
ENUMK = {"status", "ok", "img_status", "attempt", "source", "kind", "cid_kind"}


def hours(sub, day):
    d = os.path.join(ROOT, sub)
    return sorted(f for f in os.listdir(d) if f.startswith(day) and f.endswith(".jsonl"))


def empty(v):
    return v is None or v == "" or v == [] or v == {}


out = {}
for day in DAYS:
    r = {"creates": 0, "creates_hours": 0, "meta_rows": 0, "meta_hours": 0}
    fill = collections.Counter()
    enum = collections.Counter()
    mints = set()
    cmints = set()
    for f in hours("creates", day):
        r["creates_hours"] += 1
        for line in open(os.path.join(ROOT, "creates", f)):
            try:
                o = json.loads(line)
            except Exception:
                r["creates_bad"] = r.get("creates_bad", 0) + 1
                continue
            r["creates"] += 1
            cmints.add(o.get("mint"))
            for k in ("name", "symbol", "uri", "creator"):
                if o.get(k):
                    fill["c." + k] += 1
    r["creates_distinct_mints"] = len(cmints)
    for f in hours("meta", day):
        r["meta_hours"] += 1
        for line in open(os.path.join(ROOT, "meta", f)):
            try:
                o = json.loads(line)
            except Exception:
                r["meta_bad"] = r.get("meta_bad", 0) + 1
                continue
            r["meta_rows"] += 1
            mints.add(o.get("mint"))
            for k, v in o.items():
                if not empty(v):
                    fill["m." + k] += 1
                if k in ENUMK and isinstance(v, (str, int, bool)) and len(str(v)) <= 20:
                    enum["m.%s=%s" % (k, v)] += 1
                if isinstance(v, dict):
                    for s in NEST & set(v):
                        if not empty(v[s]):
                            fill["m.%s.%s" % (k, s)] += 1
    r["meta_distinct_mints"] = len(mints)
    r["fill"] = dict(sorted(fill.items()))
    r["enum"] = dict(sorted(enum.items()))
    out[day] = r
json.dump(out, sys.stdout, indent=1, sort_keys=True)
