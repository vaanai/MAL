"""P41 (p37-cto-hold) tape scan, COUNTS ONLY. See docs/research/p41-cto-hold/DECLARE.md.

Extracts, for a fixed list of mints, slim trade / create / migration rows from the allowed exploration
clean views (windows A, B, C of the DECLARE). No price path, return or P&L is computed here.

Usage: python tools/p41_cto_tape_scan.py --mints MINTS.txt --out OUT_DIR
Writes OUT_DIR/{trades,creates,migrations}.jsonl (slim rows) and OUT_DIR/scan_manifest.json.
"""
import argparse, json, os, re, subprocess, sys

CV = "/data/mal/clean-view"
# (window, block dir relative to CV, first hour incl, last hour excl) -- DECLARE "Allowed data"
SOURCES = [
    ("A", "explore-0814/w1"), ("A", "explore-0814/w2"), ("A", "explore-0814/w3"), ("A", "explore-0814/w4"),
    ("A", "explore-0814/w5"), ("A", "explore-0814/w6"), ("A", "explore-0814/w7"),
    ("B", "fresh-0903/w1"), ("B", "fresh-0903/w2"), ("B", "fresh-0903/w3"),
    ("B", "exp011-0909/b"), ("B", "exp011-0909/c"),
    ("C", "fast-pool-2026-09-18T23_2026-09-22T00"), ("C", "oracle-insample-2026-09-22_25"),
]
WINDOWS = {"A": ("2026-08-14T12", "2026-08-28T12"), "B": ("2026-09-03T12", "2026-09-15T12"), "C": ("2026-09-18T23", "2026-09-25T07")}
# per-block hour bounds where a window joins two blocks (ledger rows)
BLOCK_BOUNDS = {"fast-pool-2026-09-18T23_2026-09-22T00": ("2026-09-18T23", "2026-09-22T00"),
                "oracle-insample-2026-09-22_25": ("2026-09-22T00", "2026-09-25T07"),
                "exp011-0909/b": ("2026-09-09T12", "2026-09-15T12"), "exp011-0909/c": ("2026-09-09T12", "2026-09-15T12"),
                "fresh-0903/w1": ("2026-09-03T12", "2026-09-09T12"), "fresh-0903/w2": ("2026-09-03T12", "2026-09-09T12"),
                "fresh-0903/w3": ("2026-09-03T12", "2026-09-09T12")}
FORBID = ("meta-capture", "arena", "forward-1016", "fresh-0828", "fresh-0802", "fresh-0808", "fwdv1")
HOUR_RE = re.compile(r"-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\.zst$")
KEEP = {"trades": ("venue", "mint", "side", "sol", "quote_reserve", "base_reserve", "pool", "slot", "block_time", "signature", "event_index"),
        "creates": ("type", "mint", "creator", "slot", "block_time", "venue"),
        "migrations": ("type", "mint", "pool", "slot", "block_time", "venue")}


def hour_files(block, stream):
    d = os.path.join(CV, block, stream)
    assert not any(f in d for f in FORBID), d
    win = None
    for w, b in SOURCES:
        if b == block: win = w
    lo, hi = WINDOWS[win]
    lo, hi = max(lo, BLOCK_BOUNDS.get(block, (lo, hi))[0]), min(hi, BLOCK_BOUNDS.get(block, (lo, hi))[1])
    for f in sorted(os.listdir(d)):
        m = HOUR_RE.search(f)
        if m and lo <= m.group(1) < hi:
            yield win, m.group(1), os.path.join(d, f)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mints", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    mints = {l.strip() for l in open(a.mints) if l.strip()}
    os.makedirs(a.out, exist_ok=True)
    man = {"files": {}, "rows": {}, "hours": {}}
    for stream in ("creates", "migrations", "trades"):
        n = 0
        with open(os.path.join(a.out, f"{stream}.jsonl"), "w") as fo:
            for _, block in SOURCES:
                for win, hour, path in hour_files(block, stream):
                    man["files"][stream] = man["files"].get(stream, 0) + 1
                    man["hours"].setdefault(win, set()).add(hour)
                    p = subprocess.run(f"zstdcat '{path}' | grep -F -f '{a.mints}' || true", shell=True, capture_output=True, check=True)
                    for line in p.stdout.splitlines():
                        r = json.loads(line)
                        if r.get("mint") not in mints: continue
                        s = {k: r.get(k) for k in KEEP[stream]}
                        s["win"] = win
                        fo.write(json.dumps(s) + "\n"); n += 1
        man["rows"][stream] = n
        print(stream, n, flush=True)
    man["hours"] = {k: sorted(v) for k, v in man["hours"].items()}
    man["hour_counts"] = {k: len(v) for k, v in man["hours"].items()}
    json.dump(man, open(os.path.join(a.out, "scan_manifest.json"), "w"), indent=1)
    res = os.environ.get("MISCUSI_RESULT")
    if res:
        json.dump({"ok": True, "metrics": man["rows"] | {"hours_" + k: v for k, v in man["hour_counts"].items()}, "summary": "P41 tape scan, counts only"}, open(res, "w"))


if __name__ == "__main__":
    sys.exit(main())
