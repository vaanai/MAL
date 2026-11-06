"""r4-b-T hourly feed-bar line (DEC-031 Am.27 item 9): writer half + follower half over the 24 closed UTC hours before a registration hour R.

Binding spec: QP-RULING-1106-r4bt-prereg-risks section 2 ("A new counting tool is admissible").

Reads ONLY: the counts log (through tools/tip_hour_health.py, imported from the pinned bytes, not copied); the sampler files `tip-health-*.jsonl`, keys `t`, `s_gaps`,
`s_backlog_jumps`, `s_unresolved_dropped` only; and the pins file (same --pins semantics as `tip_hour_health.py bar`: a file, never `auto`, never absent).
Prints counts and move flags only. Read-only, no network. Exit 0 PASS, 1 FAIL, 3 INCOMPLETE, 2 input error.

  r4bt_feed_bar.py check --counts <path> --health-glob '<dir>/tip-health-*.jsonl' --pins <file> --reg-hour <ISO whole UTC hour>
  r4bt_feed_bar.py scan  --counts <path> --health-glob '<glob>' --pins <file> --from-hour <h> --to-hour <h>
"""
from __future__ import annotations

import argparse
import glob as globmod
import hashlib
import sys
import types
from pathlib import Path

WRITER_SHA256 = "9666e6d0ac18fb880ab66aa9175409b2f924a09eeb39507a31043877503dec6f"
WRITER_PATH = Path(__file__).resolve().parent / "tip_hour_health.py"
T_MAX_S = 176460.0  # the registered `bar --t-max-s`
PROGRAMS = ("pumpswap",)  # the registered `bar --programs`
KEYS = ("s_gaps", "s_backlog_jumps", "s_unresolved_dropped")
WINDOW_H = 24


class Refused(Exception):
    pass


def load_writer(expected: str = WRITER_SHA256, path: Path = WRITER_PATH) -> types.ModuleType:
    """Import tip_hour_health from the exact bytes whose sha256 was checked; refuse on a mismatch."""
    data = Path(path).read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != expected:
        raise Refused(f"tip_hour_health.py sha256 mismatch: expected {expected}, got {got}")
    mod = types.ModuleType("tip_hour_health")
    mod.__file__ = str(path)
    sys.modules["tip_hour_health"] = mod
    exec(compile(data, str(path), "exec"), mod.__dict__)
    return mod


TH = load_writer()  # import-time assertion
HOUR_MS = TH.HOUR_MS
HOUR_S = HOUR_MS // 1000


def _isint(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def load_health(pattern: str) -> list[dict]:
    """Samples [{t, s_gaps?, ...}] sorted by t, de-duplicated by t (first file/line wins). Only `t` and the three keys are ever extracted; a key that is not an int is None."""
    by: dict[int, dict] = {}
    for p in sorted(globmod.glob(pattern)):
        src = TH.read_input(p)  # applies the same path guard as the pinned tool
        if src is None:
            continue
        for r in TH._iter_jsonl(src):
            t = r.get("t") if r is not None else None
            if not _isint(t) or t in by:
                continue
            by[t] = {"t": t, **{k: (r[k] if _isint(r.get(k)) else None) for k in KEYS}}
    return [by[t] for t in sorted(by)]


def moves_by_sample(samples: list[dict]) -> list[dict]:
    """For sample i>0: key -> True if its value differs from sample i-1 (any change or reset), False if equal, None if either value is missing."""
    out = [{k: False for k in KEYS}]
    for i in range(1, len(samples)):
        d = {}
        for k in KEYS:
            a, b = samples[i - 1][k], samples[i][k]
            d[k] = None if a is None or b is None else a != b
        out.append(d)
    return out


def follower_window(samples: list[dict], mv: list[dict], r_ms: int) -> dict:
    """Follower half for R: the last sample before R-24h (baseline) through the last sample before R."""
    r_s = r_ms // 1000
    start_s = r_s - WINDOW_H * HOUR_S
    base = [i for i, s in enumerate(samples) if s["t"] < start_s]
    inwin = [i for i, s in enumerate(samples) if start_s <= s["t"] < r_s]
    if not base or not inwin:
        return {"verdict": "INCOMPLETE", "why": "no_baseline" if not base else "no_sample_in_window", "moved": {k: False for k in KEYS}}
    lo, hi = base[-1], inwin[-1]
    moved = {k: False for k in KEYS}
    incomplete = False
    for i in range(lo, hi + 1):
        if any(samples[i][k] is None for k in KEYS):
            incomplete = True
        if i > lo:
            for k in KEYS:
                if mv[i][k]:
                    moved[k] = True
    if any(moved.values()):
        return {"verdict": "FAIL", "why": "moved", "moved": moved}
    return {"verdict": "INCOMPLETE" if incomplete else "PASS", "why": "missing_key" if incomplete else "", "moved": moved}


def writer_hour_rows(rows: dict, h: int) -> dict[str, int] | None:
    """Gap rows of hour h filed under kind|prog with prog in the registered programs or `all` (the pinned bar's selection). None if no counts row for the hour."""
    if h not in rows:
        return None
    out: dict[str, int] = {}
    for k, n in TH.gap_rows_by_kind(rows[h]).items():
        prog = (k.split("|") + [""])[1]
        if prog in PROGRAMS or prog == "all":
            out[k] = n
    return out


class Inputs:
    def __init__(self, counts_path: str, health_glob: str, pins: str):
        if pins in (None, "", "auto"):
            raise ValueError("a registration bar needs the registered pins file (--pins <file>); `auto` and no pins are refused")
        self.pins = pins
        src = TH.read_input(counts_path)
        self.counts = TH._bytes(src) if src is not None else None
        self.rows = TH.load_counts(self.counts)[0] if self.counts is not None else {}
        self.samples = load_health(health_glob)
        self.mv = moves_by_sample(self.samples)

    def verdict(self, r_ms: int) -> tuple[str, str, dict]:
        res = TH.bar(self.counts, r_ms, T_MAX_S, PROGRAMS, self.pins)
        wv = res["verdict"]
        fw = follower_window(self.samples, self.mv, r_ms)
        fv = fw["verdict"]
        v = "FAIL" if "FAIL" in (wv, fv) else ("INCOMPLETE" if "INCOMPLETE" in (wv, fv) else "PASS")
        return v, wv, fw

    def hour_line(self, h: int) -> str:
        w = writer_hour_rows(self.rows, h)
        wtxt = "counts_row_missing" if w is None else (",".join(f"{k}={n}" for k, n in w.items()) or "none")
        lo, hi = h // 1000, (h + HOUR_MS) // 1000
        idx = [i for i, s in enumerate(self.samples) if lo <= s["t"] < hi]
        flags = []
        for k in KEYS:
            vals = [self.mv[i][k] for i in idx if i > 0]
            flags.append(f"{k}=" + ("move" if any(v for v in vals) else ("unknown" if any(v is None for v in vals) else "none")))
        return f"hour {TH.hour_key(h)} writer_rows[{wtxt}] samples={len(idx)} follower[{' '.join(flags)}]"

    def r_lines(self, r_ms: int) -> tuple[list[str], str]:
        v, wv, fw = self.verdict(r_ms)
        lines = [self.hour_line(r_ms - (WINDOW_H - i) * HOUR_MS) for i in range(WINDOW_H)]
        moved = ",".join(k for k in KEYS if fw["moved"][k]) or "none"
        lines.append(f"R {TH.iso(r_ms)} writer={wv} follower={fw['verdict']}{(':' + fw['why']) if fw['why'] else ''} moved={moved} verdict={v}")
        return lines, v


EXIT = {"PASS": 0, "FAIL": 1, "INCOMPLETE": 3}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "scan"):
        p = sub.add_parser(name)
        p.add_argument("--counts", required=True)
        p.add_argument("--health-glob", required=True)
        p.add_argument("--pins", required=True)
        if name == "check":
            p.add_argument("--reg-hour", required=True)
        else:
            p.add_argument("--from-hour", required=True)
            p.add_argument("--to-hour", required=True)
    a = ap.parse_args(argv)
    try:
        inp = Inputs(a.counts, a.health_glob, a.pins)
        if a.cmd == "check":
            r = TH.parse_instant(a.reg_hour)
            if r % HOUR_MS:
                raise ValueError("--reg-hour must be a whole UTC hour")
            lines, v = inp.r_lines(r)
            print("\n".join(lines))
            return EXIT[v]
        h0, h1 = TH.parse_instant(a.from_hour), TH.parse_instant(a.to_hour)
        if h0 % HOUR_MS or h1 % HOUR_MS:
            raise ValueError("--from-hour and --to-hour must be whole UTC hours")
        if h1 < h0:
            raise ValueError("--to-hour before --from-hour")
        # per-hour lines for every hour any R in the range looks at would be 24x redundant: print each hour once, then one verdict line per R
        out, verdicts = [], []
        for h in range(h0 - WINDOW_H * HOUR_MS, h1, HOUR_MS):
            out.append(inp.hour_line(h))
        for r in range(h0, h1 + HOUR_MS, HOUR_MS):
            v, wv, fw = inp.verdict(r)
            verdicts.append((r, v))
            moved = ",".join(k for k in KEYS if fw["moved"][k]) or "none"
            out.append(f"R {TH.iso(r)} writer={wv} follower={fw['verdict']} moved={moved} verdict={v}")
        clean = next((TH.iso(r) for r, v in verdicts if v == "PASS"), None)
        out.append(f"earliest_clean_R {clean if clean else 'none_in_range'}")
        print("\n".join(out))
        return 0
    except (Refused, TH.Refused) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
