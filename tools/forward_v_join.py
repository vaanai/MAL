#!/usr/bin/env python3
"""Join event-V from forward-1002ev onto forward-1002 rows (DEC-016 Amendment 8, EXP-024 Amendment 1).

forward-1002 (job #382) was walked without --event-v, so its PumpSwap rows carry no virtual quote reserve V. The
forward-1002ev walk (scripts/research/forward-walk-ev.sh) re-walks the last week with the event-V decoder. This tool
carries V across, row by row, and refuses to trust an hour it cannot check.

    python3 -m tools.forward_v_join pins
    python3 -m tools.forward_v_join check --from H --to H --hash-only                  # allowed BEFORE the FINAL
    python3 -m tools.forward_v_join check --from H --to H --fallback-out F --report-out R   # after the FINAL
    python3 -m tools.forward_v_join join  --from H --to H --out-dir D [--emit v|rows]       # after the FINAL

[H, H) is hour-aligned (YYYY-MM-DDTHH), end exclusive.

SEAL. forward-1002 and forward-1002ev are sealed until the DEC-016 FINAL (A) is written: nothing reads their values.
So `check` without --hash-only, and `join`, refuse unless the EXP-012 FINAL marker is in the external FINAL ledger
(default /data/mal/exp012-forward/FINAL_READS.jsonl, a line with final=true, no test_window). `--hash-only` is the
only mode that runs before it. It reads the rows in process and prints nothing but, per hour, the md5 verdict, the
row counts, the 1:1 match rate, the decision and a reason from a fixed list. It prints no row value, no signature
and no pool id, and it writes no file; an exception prints only its class name. There is no flag that skips the gate.

WHAT IT CHECKS, per hour, on the raw JSONL (trades-<hour>.jsonl.zst of each walk):
  1. Each hour of each walk is usable: sealed in checkpoint.json, a last OK line in verify.jsonl, and the file bytes
     hash to that line's sha256 (ev: no bad lines either). An hour that was not walked, not sealed, not verified or
     whose file changed is a BAD hour. An ev hour that was never walked (the walk hit its credit cap, or stopped) is
     a bad hour, not an empty one.
  2. md5. The md5 of the ev rows with the V fields (observe.trade_decode.EVENT_V_KEYS) dropped equals the md5 of the
     forward-1002 rows, both over canonical lines (sort_keys, compact) in file order. Reported, not the decision.
  3. The join, on the key (slot, signature, event_index). A pair is matched 1:1 when the key is on both sides once,
     the venue is equal, and sol_lamports, token_raw, quote_reserve and base_reserve are equal.
  4. The 1:1 match rate = matched / max(rows in forward-1002, rows in ev), over all trade rows and over the PumpSwap
     rows. An hour with either rate below 99.5% is REFUSED (QP pin), as is an hour with a duplicate key, a bad line or
     no rows. A refused hour's V is not used at all.

A usable hour gets V for each 1:1-matched row that carries it. Its other PumpSwap rows (no match, a field that
differs, or a matched ev row with no V) go to the FALLBACK list one by one. For a refused or bad hour the fallback
list holds every PumpSwap row of the forward-1002 hour (when that hour is readable). The fallback list is
{hour, slot, signature, event_index, why} per line: the rows whose V must be rebuilt by getTransaction (EXP-024
Amendment 1). Bonding rows need no V; they get ix_name when matched.

Decoder pin. `pins` and every `check`/`join` verify that observe/trade_decode.py, observe/trade_store.py and
tools/pump_history_backfill.py hash (git blob sha1) to PINNED_BLOBS, the blobs on main at a3e923c that forward-walk-ev.sh
and forward-walk2.sh (walk 2) both run through `python -m tools.pump_history_backfill --event-v`. A different blob
is a refusal (exit 2), not a warning. A decoder change needs a dated amendment and a new pin.

Output files are never written inside either walk dir, never overwrite, and are written whole or not at all.
Exit: 0 every hour usable, 1 some hour refused or bad (informational), 2 a refusal (seal, pin, usage).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Sequence

from observe.trade_decode import EVENT_V_KEYS

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BASE = Path("/data/mal/blocks/forward-1002")
DEFAULT_EV = Path("/data/mal/blocks/forward-1002ev")
DEFAULT_LEDGER = Path("/data/mal/exp012-forward/FINAL_READS.jsonl")
PRIMARY_EXPERIMENT = "EXP-012"

MIN_MATCH_RATE = 0.995
KEY_FIELDS = ("slot", "signature", "event_index")
MATCH_FIELDS = ("sol_lamports", "token_raw", "quote_reserve", "base_reserve")
V_FIELD = "virtual_quote_reserves"  # the name EVENT_V_KEYS carries; the one PumpSwap rows need
VENUE_PUMPSWAP = "pumpswap"

# Git blob shas (sha1 of "blob N\0" + bytes, what `git hash-object` prints) on main at a3e923c.
PINNED_BLOBS: dict[str, str] = {
    "observe/trade_decode.py": "238942a6b3c5425389eddfde4d11268c300acbec",
    "observe/trade_store.py": "ea4e11eddf9f034e3bc7318ce8743337d753f350",
    "tools/pump_history_backfill.py": "9a8bebb32adcf86de060b55f5a08110d11c0a550",
}

HOUR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")
# Every reason an hour can carry. Printed in hash-only mode, so it is a fixed list and never free text.
REASONS = (
    "ok", "base_not_walked", "base_not_sealed", "base_not_verified", "base_sha_mismatch",
    "ev_not_walked", "ev_not_sealed", "ev_not_verified", "ev_sha_mismatch", "ev_bad_lines",
    "read_error", "bad_lines", "duplicate_keys", "empty_hour", "below_threshold",
)


class Refused(Exception):
    """A refusal before or instead of a read (seal, pin, usage). Exit 2."""


class HourReadError(Exception):
    pass


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def pin_status(repo: Path = REPO) -> list[dict[str, Any]]:
    out = []
    for rel, want in PINNED_BLOBS.items():
        p = repo / rel
        got = git_blob_sha(p) if p.is_file() else None
        out.append({"file": rel, "pinned": want, "actual": got, "ok": got == want})
    return out


def require_pins(repo: Path = REPO) -> list[dict[str, Any]]:
    st = pin_status(repo)
    bad = [x["file"] for x in st if not x["ok"]]
    if bad:
        raise Refused(f"decoder pin: {', '.join(bad)} differ from PINNED_BLOBS; the join is for the decoder walk 2 uses. "
                      "A decoder change needs a dated amendment and a new pin.")
    return st


# ---- the seal ----------------------------------------------------------------------------------------------------

def final_marker(ledger: Path) -> dict[str, Any]:
    """The EXP-012 FINAL marker in the external FINAL ledger (same file tools/exp012_forward.py appends to), or Refused.

    A marker is a line with final=true for EXP-012 (the field absent counts as EXP-012) and no test_window."""
    if not ledger.is_file():
        raise Refused(f"sealed until the DEC-016 FINAL: no FINAL ledger at {ledger}. Only `check --hash-only` runs before it.")
    text = ledger.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise Refused(f"{ledger} ends with a torn line; repair it first")
    found: dict[str, Any] | None = None
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            raise Refused(f"{ledger} line {n} is not valid JSON") from None
        if (isinstance(doc, dict) and doc.get("final") is True and not doc.get("test_window")
                and doc.get("experiment", PRIMARY_EXPERIMENT) == PRIMARY_EXPERIMENT):
            found = doc
    if found is None:
        raise Refused("sealed until the DEC-016 FINAL: the EXP-012 FINAL marker is not in the FINAL ledger. "
                      "Only `check --hash-only` runs before it.")
    return found


# ---- hours and files ---------------------------------------------------------------------------------------------

def hour_list(start: str, end: str) -> list[str]:
    for h in (start, end):
        if not HOUR_RE.match(h):
            raise Refused(f"hour {h!r} is not YYYY-MM-DDTHH")
    t = datetime.strptime(start, "%Y-%m-%dT%H")
    stop = datetime.strptime(end, "%Y-%m-%dT%H")
    if stop <= t:
        raise Refused("--to must be after --from")
    out = []
    while t < stop:
        out.append(t.strftime("%Y-%m-%dT%H"))
        t += timedelta(hours=1)
    return out


def hour_file(walk_dir: Path, hour: str) -> Path | None:
    for name in (f"trades-{hour}.jsonl.zst", f"trades-{hour}.jsonl"):
        p = walk_dir / "trades" / name
        if p.is_file():
            return p
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def hour_state(walk_dir: Path, hour: str, *, strict: bool) -> tuple[Path | None, str]:
    """(trades file, state). state is "ok" or one of not_walked, not_sealed, not_verified, sha_mismatch, bad_lines.

    Usable = sealed in checkpoint.json, the hour's LAST line in verify.jsonl has no issues and a trades sha256 (and no
    duplicates; with strict, no bad lines), and the file's bytes hash to it. Reads no row."""
    try:
        cp = json.loads((walk_dir / "checkpoint.json").read_text(encoding="utf-8"))
        entry = (cp.get("hours") or {}).get(hour)
    except (OSError, ValueError, AttributeError):
        entry = None
    path = hour_file(walk_dir, hour)
    if not isinstance(entry, dict) or path is None:
        return None, "not_walked"
    if entry.get("status") != "sealed":
        return None, "not_sealed"
    last: dict[str, Any] | None = None
    try:
        for line in (walk_dir / "verify.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("hour") == hour:
                last = rec
    except OSError:
        last = None
    if last is None or last.get("issues") != []:
        return None, "not_verified"
    sha = last.get("sha256")
    if not isinstance(sha, dict) or not isinstance(sha.get("trades"), str):
        return None, "not_verified"
    content = last.get("content")
    if isinstance(content, dict):
        for stats in content.values():
            if isinstance(stats, dict) and stats.get("duplicates") not in (0, None):
                return None, "not_verified"
    if strict:
        bad = [last.get("bad_lines")] + [s.get("bad_lines") for s in (content or {}).values() if isinstance(s, dict)]
        if any(b not in (None, 0) for b in bad):
            return None, "bad_lines"
    if sha256_file(path) != sha["trades"]:
        return None, "sha_mismatch"
    return path, "ok"


class RowReader:
    """Parsed rows of one trades file, in file order. Counts (does not raise on) bad lines; raises HourReadError if the
    zstd stream does not end clean. Holds no more than one line."""

    def __init__(self, path: Path) -> None:
        self.path, self.bad_lines = path, 0

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if self.path.name.endswith(".zst"):
            proc = subprocess.Popen(["zstd", "-dc", "-q", str(self.path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert proc.stdout is not None
            lines: Any = proc.stdout
        else:
            proc = None
            lines = self.path.open("rb")
        try:
            for raw in lines:
                if not raw.strip():
                    continue
                if b"\x00" in raw:
                    self.bad_lines += 1
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    self.bad_lines += 1
                    continue
                if not isinstance(row, dict):
                    self.bad_lines += 1
                    continue
                yield row
        finally:
            if proc is not None:
                proc.stdout.close()  # type: ignore[union-attr]
                rc = proc.wait()
                if rc != 0:
                    raise HourReadError("zstd")
            else:
                lines.close()


def canonical(row: dict[str, Any]) -> bytes:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def row_key(row: dict[str, Any]) -> tuple[int, str, int] | None:
    s, g, i = (row.get(k) for k in KEY_FIELDS)
    if type(s) is int and isinstance(g, str) and type(i) is int:
        return (s, g, i)
    return None


def match_fields(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("venue"),) + tuple(row.get(f) for f in MATCH_FIELDS)


def v_part(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in EVENT_V_KEYS if k in row}


# ---- one hour ----------------------------------------------------------------------------------------------------

@dataclass
class HourResult:
    hour: str
    usable: bool = False
    reason: str = "ok"
    md5_match: bool | None = None
    n_base: int = 0
    n_ev: int = 0
    n_base_ps: int = 0
    n_ev_ps: int = 0
    matched: int = 0
    matched_ps: int = 0
    field_mismatch: int = 0
    unkeyed: int = 0
    v_missing: int = 0
    rate: float | None = None
    rate_ps: float | None = None
    fallback: list[tuple[int, str, int, str]] = field(default_factory=list)  # (slot, signature, event_index, why)
    fallback_n: int = 0
    base_readable: bool = False
    v_rows: int = 0  # rows written by `join` (or that would be)

    def public(self) -> dict[str, Any]:
        """Counts and verdicts only. This is what hash-only prints and the report holds: no key, no value."""
        return {
            "hour": self.hour, "usable": self.usable, "reason": self.reason, "md5_match": self.md5_match,
            "rows_base": self.n_base, "rows_ev": self.n_ev, "pumpswap_base": self.n_base_ps, "pumpswap_ev": self.n_ev_ps,
            "matched_1to1": self.matched, "pumpswap_matched": self.matched_ps, "rate": self.rate, "rate_pumpswap": self.rate_ps,
            "field_mismatch": self.field_mismatch, "unkeyed": self.unkeyed, "v_missing": self.v_missing,
            "fallback_rows": self.fallback_n, "v_rows": self.v_rows,
        }


def _ratio(a: int, b: int) -> float:
    return 1.0 if b == 0 else a / b


class VSink:
    """Writes one hour's V (or joined) rows to a temp zstd file; finish() renames it, discard() deletes it."""

    def __init__(self, final: Path) -> None:
        if final.exists():
            raise Refused(f"{final} exists; the join never overwrites")
        self.final = final
        self.tmp = final.with_name(final.name + ".tmp")
        final.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(["zstd", "-q", "-3", "-T1", "-f", "-o", str(self.tmp)], stdin=subprocess.PIPE)
        assert self.proc.stdin is not None
        self.rows = 0

    def write(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n")
        self.rows += 1

    def _close(self) -> int:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        return self.proc.wait()

    def finish(self) -> Path:
        if self._close() != 0:
            self.tmp.unlink(missing_ok=True)
            raise HourReadError("zstd_write")
        os.rename(self.tmp, self.final)
        return self.final

    def discard(self) -> None:
        try:
            self._close()
        finally:
            self.tmp.unlink(missing_ok=True)


def check_hour(
    base_dir: Path, ev_dir: Path, hour: str, *, sink: VSink | None = None, emit: str = "v",
) -> HourResult:
    """Check (and, with a sink, join) one hour. Values stay in this function: the result carries counts and the
    (slot, signature, event_index) of rows whose V must come from getTransaction. The caller decides whether that list
    is ever written (main writes it only after the FINAL).

    The caller finishes the sink when the result is usable and discards it otherwise."""
    res = HourResult(hour)
    base_path, base_st = hour_state(base_dir, hour, strict=False)
    if base_path is None:
        res.reason = "base_" + base_st  # nothing to join and no rows to list
        return res
    ev_path, ev_st = hour_state(ev_dir, hour, strict=True)
    ev_reason: str | None = None if ev_path is not None else "ev_" + ev_st  # None: the ev hour is usable so far

    # Pass A: the ev rows into a map by key. A bad ev hour is not read.
    ev_map: dict[tuple[int, str, int], tuple[tuple[Any, ...], dict[str, Any]]] = {}
    md5_ev = hashlib.md5()
    dup = False
    if ev_path is not None:
        try:
            rd = RowReader(ev_path)
            for row in rd:
                res.n_ev += 1
                res.n_ev_ps += 1 if row.get("venue") == VENUE_PUMPSWAP else 0
                md5_ev.update(canonical({k: v for k, v in row.items() if k not in EVENT_V_KEYS}) + b"\n")
                key = row_key(row)
                if key is None:
                    res.unkeyed += 1
                elif key in ev_map:
                    dup = True
                else:
                    vp = v_part(row)
                    ev_map[key] = (match_fields(row), vp if sink is not None else ({V_FIELD: 1} if V_FIELD in vp else {}))
            if rd.bad_lines:
                ev_reason = "ev_bad_lines"
        except HourReadError:
            ev_reason = "read_error"

    # Pass B: the base rows, matched against the map.
    md5_base = hashlib.md5()
    base_dup = False
    seen_base: set[tuple[int, str, int]] = set()
    pending: list[tuple[int, str, int]] = []                    # every keyed base PumpSwap row
    per_row: list[tuple[int, str, int, str]] = []               # the ones without V in an otherwise usable hour
    base_bad = False
    try:
        rd = RowReader(base_path)
        for row in rd:
            res.n_base += 1
            ps = row.get("venue") == VENUE_PUMPSWAP
            res.n_base_ps += 1 if ps else 0
            md5_base.update(canonical(row) + b"\n")
            key = row_key(row)
            if key is None:
                res.unkeyed += 1
                continue
            if ps:
                pending.append(key)
            if key in seen_base:
                base_dup = True
            seen_base.add(key)
            if ev_reason is not None:
                continue
            e = ev_map.get(key)
            if e is None:
                if ps:
                    per_row.append((*key, "unmatched"))
                continue
            ev_fields, vp = e
            if ev_fields != match_fields(row):
                res.field_mismatch += 1
                if ps:
                    per_row.append((*key, "field_mismatch"))
                continue
            res.matched += 1
            if ps:
                res.matched_ps += 1
                if V_FIELD not in vp:
                    res.v_missing += 1
                    per_row.append((*key, "v_missing"))
            if sink is not None and vp:
                out = dict(row) if emit == "rows" else {"slot": key[0], "signature": key[1], "event_index": key[2]}
                out.update(vp)
                sink.write(out)
                res.v_rows += 1
        base_bad = bool(rd.bad_lines)
        res.base_readable = True
    except HourReadError:
        res.base_readable = False
        res.reason = "read_error"
        return res

    if ev_reason is None:
        res.md5_match = res.n_base == res.n_ev and md5_base.hexdigest() == md5_ev.hexdigest()
        res.rate = _ratio(res.matched, max(res.n_base, res.n_ev))
        res.rate_ps = _ratio(res.matched_ps, max(res.n_base_ps, res.n_ev_ps))

    if ev_reason is not None:
        reason = ev_reason
    elif base_bad:
        reason = "bad_lines"
    elif res.n_base == 0 and res.n_ev == 0:
        reason = "empty_hour"
    elif dup or base_dup:
        reason = "duplicate_keys"
    elif (res.rate or 0.0) < MIN_MATCH_RATE or (res.rate_ps or 0.0) < MIN_MATCH_RATE:
        reason = "below_threshold"
    else:
        reason = "ok"
    res.reason, res.usable = reason, reason == "ok"
    if res.usable:
        res.fallback = per_row
    elif reason != "bad_lines":
        res.fallback = [(*k, "hour_" + reason) for k in pending]
    res.fallback_n = len(res.fallback)
    return res


# ---- the run ------------------------------------------------------------------------------------------------------

def run_check(
    base_dir: Path, ev_dir: Path, hours: Sequence[str], *, out_dir: Path | None = None, emit: str = "v",
) -> list[HourResult]:
    results = []
    for h in hours:
        sink = VSink(out_dir / f"{'v' if emit == 'v' else 'joined'}-{h}.jsonl.zst") if out_dir is not None else None
        try:
            r = check_hour(base_dir, ev_dir, h, sink=sink, emit=emit)
        except BaseException:
            if sink is not None:
                sink.discard()
            raise
        if sink is not None:
            sink.finish() if r.usable else sink.discard()
        results.append(r)
    return results


def iter_joined_rows(base_dir: Path, v_dir: Path, hour: str) -> Iterator[dict[str, Any]]:
    """Consumer helper: forward-1002's rows of `hour` with V merged in from `join`'s v-<hour>.jsonl.zst, in file order.
    An hour `join` refused has no v file: its rows come back without V, and the caller uses the fallback list."""
    base = hour_file(base_dir, hour)
    if base is None:
        raise HourReadError("base_not_walked")
    vmap: dict[tuple[int, str, int], dict[str, Any]] = {}
    vfile = hour_file_named(v_dir, f"v-{hour}")
    if vfile is not None:
        for rec in RowReader(vfile):
            key = row_key(rec)
            if key is not None:
                vmap[key] = {k: rec[k] for k in EVENT_V_KEYS if k in rec}
    for row in RowReader(base):
        key = row_key(row)
        if key is not None and key in vmap:
            row = dict(row)
            row.update(vmap[key])
        yield row


def hour_file_named(directory: Path, stem: str) -> Path | None:
    for ext in (".jsonl.zst", ".jsonl"):
        p = directory / (stem + ext)
        if p.is_file():
            return p
    return None


# ---- output files -------------------------------------------------------------------------------------------------

def write_new(path: Path, data: bytes) -> None:
    """Whole or not at all, and never over an existing file."""
    if path.exists():
        raise Refused(f"{path} exists; this tool never overwrites")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.rename(tmp, path)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def format_public(r: HourResult) -> str:
    md5 = "n/a" if r.md5_match is None else ("match" if r.md5_match else "MISMATCH")
    rate = "n/a" if r.rate is None else f"{r.rate:.6f}"
    rate_ps = "n/a" if r.rate_ps is None else f"{r.rate_ps:.6f}"
    verdict = "usable" if r.usable else f"refused({r.reason})"
    return (f"{r.hour} {verdict} md5={md5} rows_base={r.n_base} rows_ev={r.n_ev} matched={r.matched} rate={rate} "
            f"ps_base={r.n_base_ps} ps_ev={r.n_ev_ps} ps_matched={r.matched_ps} ps_rate={rate_ps} "
            f"field_mismatch={r.field_mismatch} v_missing={r.v_missing} unkeyed={r.unkeyed} fallback_rows={r.fallback_n}")


def summary_line(results: Sequence[HourResult]) -> str:
    n = len(results)
    ok = sum(r.usable for r in results)
    md5 = sum(1 for r in results if r.md5_match)
    return (f"hours={n} usable={ok} refused={n - ok} md5_match={md5} "
            f"min_rate={min((r.rate for r in results if r.rate is not None), default=None)}")


def build_report(results: Sequence[HourResult], args: argparse.Namespace, marker: dict[str, Any],
                 pins: list[dict[str, Any]], outputs: dict[str, str]) -> dict[str, Any]:
    reasons: dict[str, int] = {}
    for r in results:
        reasons[r.reason] = reasons.get(r.reason, 0) + 1
    return {
        "tool": "tools/forward_v_join.py", "from": args.start, "to": args.end,
        "base": str(args.base), "ev": str(args.ev), "min_match_rate": MIN_MATCH_RATE,
        "match_fields": list(MATCH_FIELDS), "key": list(KEY_FIELDS), "v_keys": list(EVENT_V_KEYS),
        "decoder_pins": pins, "final_marker": {k: marker.get(k) for k in ("utc_time", "rows_sha256", "lock_sha256")},
        "hours": [r.public() for r in results], "reasons": reasons, "summary": summary_line(results),
        "outputs_sha256": outputs,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pins", help="print the decoder pins and whether the working tree matches (hashes only)")
    for name in ("check", "join"):
        p = sub.add_parser(name)
        p.add_argument("--base", type=Path, default=DEFAULT_BASE)
        p.add_argument("--ev", type=Path, default=DEFAULT_EV)
        p.add_argument("--from", dest="start", required=True)
        p.add_argument("--to", dest="end", required=True)
        p.add_argument("--final-ledger", type=Path, default=DEFAULT_LEDGER)
        if name == "check":
            p.add_argument("--hash-only", action="store_true",
                           help="the only mode before the FINAL: md5 verdict, counts and rates per hour; no values, no files")
            p.add_argument("--fallback-out", type=Path, help="after the FINAL: the (hour, slot, signature, event_index, why) list")
            p.add_argument("--report-out", type=Path, help="after the FINAL: counts-only JSON report")
        else:
            p.add_argument("--out-dir", type=Path, required=True)
            p.add_argument("--emit", choices=("v", "rows"), default="v",
                           help="v: key + V fields per matched row (default); rows: the forward-1002 row with V merged")
            p.add_argument("--fallback-out", type=Path, help="default: OUT/fallback.jsonl")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "pins":
            st = pin_status()
            for x in st:
                print(f"{x['file']} pinned={x['pinned']} actual={x['actual']} {'ok' if x['ok'] else 'DIFFERS'}")
            return 0 if all(x["ok"] for x in st) else 2
        hours = hour_list(args.start, args.end)
        hash_only = args.cmd == "check" and args.hash_only
        if hash_only and (args.fallback_out or args.report_out):
            raise Refused("--hash-only writes nothing: drop --fallback-out/--report-out (they run after the FINAL)")
        pins = require_pins()
        marker: dict[str, Any] = {}
        if not hash_only:
            marker = final_marker(args.final_ledger)
        if args.cmd == "join":
            for d in (args.base, args.ev):
                if _inside(args.out_dir, d):
                    raise Refused("--out-dir is inside a walk dir; the sealed dirs are never written")
        out_dir = args.out_dir if args.cmd == "join" else None
        fb_path = getattr(args, "fallback_out", None)
        rep_path = getattr(args, "report_out", None)
        if args.cmd == "join":
            fb_path = fb_path or args.out_dir / "fallback.jsonl"
            rep_path = args.out_dir / "join-report.json"
        for f in (fb_path, rep_path):
            if f is not None and (_inside(f, args.base) or _inside(f, args.ev)):
                raise Refused("an output path is inside a walk dir; the sealed dirs are never written")
            if f is not None and f.exists():
                raise Refused(f"{f} exists; this tool never overwrites")
        results = run_check(args.base, args.ev, hours, out_dir=out_dir, emit=getattr(args, "emit", "v"))
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # the class only: a message could carry a row
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2

    for r in results:
        print(format_public(r))
    print(summary_line(results))
    if hash_only:
        return 0 if all(r.usable for r in results) else 1

    outputs: dict[str, str] = {}
    try:
        if fb_path is not None:
            lines = []
            for r in results:
                for s, g, i, why in r.fallback:
                    lines.append(json.dumps({"hour": r.hour, "slot": s, "signature": g, "event_index": i, "why": why},
                                            separators=(",", ":")))
            write_new(fb_path, ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8"))
            outputs[str(fb_path)] = sha256_file(fb_path)
            print(f"fallback rows: {sum(len(r.fallback) for r in results)} -> {fb_path}")
        if args.cmd == "join":
            for r in results:
                if r.usable:
                    p = hour_file_named(args.out_dir, f"{'v' if args.emit == 'v' else 'joined'}-{r.hour}")
                    if p is not None:
                        outputs[str(p)] = sha256_file(p)
        if rep_path is not None:
            rep = build_report(results, args, marker, pins, outputs)
            write_new(rep_path, (json.dumps(rep, sort_keys=True, indent=1) + "\n").encode("utf-8"))
            print(f"report -> {rep_path}")
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0 if all(r.usable for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
