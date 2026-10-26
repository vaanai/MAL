#!/usr/bin/env python3
"""HV4 worst-in-slot (WIS) export, DEC-031 Amendment 15 item 6. PAPER ONLY. Runs only for a bot whose recorded read is PASS. Report-only: it never changes the verdict.

    python -m tools.hv4_wis_export --bot BOT --instant BOT.instant.json --reads-dir DIR --tape-dir DIR --out-dir DIR
                                   --ledger-root DIR --registry FILE --health T.jsonl --health-sha SHA [...] --restarts R.jsonl --as-of-ms MS

WHAT IT DOES (QP-VERDICT-915, item 6 conditions (a) to (d); QP-VERDICT-915c)
  1. Refuses unless <reads-dir>/<bot>.json is the bot's recorded read and its status is PASS (a FAIL, NOT_DECIDABLE, REPORT_ONLY or missing record: refused, nothing
     is computed or written). Refuses if <out-dir>/<bot>.wis.json already exists.
  2. Re-runs the recorded read's own path (tools.hv4_am15_read.run: the same pins, the same instant file, the same B recomputation, the Am.6 cut and exclusions,
     the pinned arena_score a508ed79) with exactly two in-process changes: arena_score.book_cells also hands this tool the deciding stake's trade rows, and
     arena_score.record_read is a no-op (the recorded read is not touched). The trade rows are the scored trade set itself, not a second filter.
  3. REPRODUCTION. The export must reproduce the recorded read: the same B (instant file = recorded read boundary = am15 block = am6 block), the same n, the same
     four 1.9 s sub-cells' n, mean_sol and total_sol, and the same Am.6 cut and exclusions sha256. Otherwise REFUSED, nothing written.
  4. Writes the trade set once (<bot>.wis-trades.jsonl, ledger rows of kind "trade", O_EXCL, 0444; a rerun after a failed later step accepts only identical bytes).
  5. Runs `python -m tools.arena_worst_gaps` (#815) as a subprocess on that file and the tape dir. rc must be 0 (rc 4 = some trade left out = "worst-in-slot not
     measured": refused, no wis.json). It reads local tape files only: no Helius, no RPC, 0 credits (recorded as such in the file).
  6. Runs arena_score.worst_in_slot_cells (disk copy, sha256 c4859323, the same helper code as the pinned a508ed79 plus report-only additions) over the same trade rows and the
     gaps; every deciding trade must have a gap.
  7. Writes <bot>.wis.json ONCE (O_EXCL, 0444): B, n, sub-cells, the reproduction checks, the file shas, the worst-in-slot figure. Item 6: a worst-in-slot mean <= 0, or
     not measured, means no canary proposal; the file says which, as a fact about the figure and not as a verdict on the read.
No book has cleared the promotion gate. Nothing in this tool says a book makes money.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator, Sequence

from tools import arena_score as S_DISK  # disk copy: worst_in_slot_cells, load_worst_gaps. Never used to score the read.
from tools import hv4_am15_read as W
from tools import hv4_read_instant as F

SCHEMA = "hv4_wis_v1"
ARENA_SCORE_DISK_SHA256 = "c4859323622794f5f97a131eb32256c905ca2ab6c1adf8ab21568fc2a98f27f3"
REPO_ROOT = Path(__file__).resolve().parent.parent
GAPS_CMD = (sys.executable, "-m", "tools.arena_worst_gaps")


class Refused(Exception):
    pass


def canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha_file(p: str | Path) -> str:
    return _sha_bytes(Path(p).read_bytes())


def write_once(path: str | Path, data: bytes) -> None:
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        raise Refused(f"{path} exists: write-once") from None
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


# ------------------------------------------------------------------------------------------------------------------------------ the recorded read
def load_recorded_pass(reads_dir: str | Path, bot: str) -> tuple[dict, str]:
    """The bot's recorded read, only if its status is PASS."""
    p = Path(reads_dir) / f"{bot}.json"
    try:
        raw = p.read_bytes()
        rec = json.loads(raw)
    except (OSError, ValueError) as ex:
        raise Refused(f"no readable recorded read for {bot} at {p}: {ex}") from None
    if not isinstance(rec, dict) or rec.get("bot_id") != bot:
        raise Refused(f"{p} is not the recorded read of {bot}")
    if rec.get("status") != "PASS":
        raise Refused(f"{bot}: the recorded read status is {rec.get('status')!r}, not PASS: no worst-in-slot export (DEC-031 Am.15 item 6)")
    dec = rec.get("deciding") or {}
    subs = dec.get("subcells") or []
    book = (rec.get("books") or {}).get(str(dec.get("stake")))
    if len(subs) != 4 or not book or not all(k in (book.get("cells") or {}) for k in subs):
        raise Refused(f"{bot}: the recorded read lacks the four deciding sub-cells")
    if not isinstance(rec.get("read_boundary_ms"), int) or not isinstance((rec.get("am15") or {}).get("read_instant_ms"), int):
        raise Refused(f"{bot}: the recorded read carries no Amendment 15 boundary")
    return rec, _sha_bytes(raw)


def recorded_figures(rec: dict) -> dict[str, Any]:
    dec = rec["deciding"]
    book = rec["books"][str(dec["stake"])]
    return {"read_boundary_ms": rec["read_boundary_ms"], "stake": dec["stake"], "n": book["n_trades"], "rules": list(book["rules"]),
            "subcells": {k: {f: book["cells"][k][f] for f in ("n", "mean_sol", "total_sol")} for k in sorted(dec["subcells"])},
            "cut_sha256": (rec.get("am6") or {}).get("cut_sha256"), "exclusions_sha256": (rec.get("am6") or {}).get("exclusions_sha256")}


# ------------------------------------------------------------------------------------------------------------------------------ re-run with capture
@contextlib.contextmanager
def capture_deciding_trades(stake: float) -> Iterator[dict]:
    """In this process only: the pinned scorer's book_cells also hands over the deciding stake's trades, and record_read writes nothing. Restored on exit."""
    S = W.pinned_score()
    got: dict[str, Any] = {}
    orig_bc, orig_rec = S.book_cells, S.record_read

    def bc(trades, rules):
        out = orig_bc(trades, rules)
        if trades and float(trades[0]["stake_sol"]) == float(stake):
            # The Am.6 parity guard also calls score_bot (on the unfiltered-by-status rows) before the scored call; the LAST call is the scored one, and
            # reproduce() checks that its cells are exactly the scored book's cells.
            got["trades"], got["rules"], got["cells"], got["calls"] = [dict(r) for r in trades], list(rules), out, got.get("calls", 0) + 1
        return out

    S.book_cells, S.record_read = bc, (lambda reads_dir, res: Path(os.devnull))
    try:
        yield got
    finally:
        S.book_cells, S.record_read = orig_bc, orig_rec


def reproduce(rec: dict, instant_path: str, ledger_root: str, registry: str, bot: str, health_texts: Sequence[str], health_shas: Sequence[str], as_of_ms: int,
              restarts_text: str) -> tuple[list[dict], dict[str, Any]]:
    """The scored deciding trade set, and the reproduction checks. Refused unless every check holds."""
    want = recorded_figures(rec)
    ins, isha = W.load_instant(instant_path)
    b = want["read_boundary_ms"]
    for name, v in (("instant file", ins.get("read_instant_ms")), ("am15 block", rec["am15"]["read_instant_ms"]), ("am6 block", (rec.get("am6") or {}).get("read_boundary_ms"))):
        if v != b:
            raise Refused(f"B mismatch: recorded read boundary {b}, {name} {v}")
    with capture_deciding_trades(want["stake"]) as got:
        try:
            code, _lines, res = W.run(instant_path, ledger_root, registry, bot, list(health_texts), list(health_shas), as_of_ms, restarts_text, None, True, str(Path(os.devnull)))
        except (W.Refused, W.R.Refused, W.F.Refused) as ex:
            raise Refused(f"the read could not be re-run: {ex}") from None
    fresh = (res or {}).get("bots", {}).get(bot)
    if code != 0 or not fresh or "trades" not in got:
        raise Refused("the read could not be re-run to a scored book at the deciding stake")
    trades = got["trades"]
    dk = str(want["stake"])
    fbook = fresh["books"][dk]
    checks = {"status_pass": fresh.get("status") == "PASS", "B_equal": fresh.get("read_boundary_ms") == b, "n_equal": len(trades) == want["n"] == fbook["n_trades"],
              "rules_equal": got["rules"] == want["rules"], "captured_cells_are_the_scored_cells": got["cells"] == fbook["cells"],
              "cut_sha_equal": (fresh.get("am6") or {}).get("cut_sha256") == want["cut_sha256"],
              "exclusions_sha_equal": (fresh.get("am6") or {}).get("exclusions_sha256") == want["exclusions_sha256"]}
    sub_ok = {}
    for k, w in want["subcells"].items():
        c = fbook["cells"].get(k) or {}
        sub_ok[k] = all(c.get(f) == w[f] for f in ("n", "mean_sol", "total_sol")) and c.get("n") == len(trades)
    checks["subcells_equal"] = all(sub_ok.values()) and len(sub_ok) == 4
    bad = sorted(k for k, v in checks.items() if not v) + sorted(f"subcell {k}" for k, v in sub_ok.items() if not v)
    if bad:
        raise Refused("the export does not reproduce the recorded read (" + ", ".join(bad) + "): refused, nothing written")
    ids = [r["trade_id"] for r in trades]
    if len(set(ids)) != len(ids) or any(r.get("kind") != "trade" for r in trades):
        raise Refused("the captured trade set has a duplicate trade_id or a row that is not kind 'trade'")
    return trades, {"checks": dict(checks, subcells=sub_ok), "instant_file_sha256": isha, "recorded": want}


# ------------------------------------------------------------------------------------------------------------------------------ gaps and the figure
def trades_bytes(trades: Sequence[dict]) -> bytes:
    return "".join(canon(r) + "\n" for r in trades).encode()


def write_trades_once(path: Path, trades: Sequence[dict]) -> str:
    data = trades_bytes(trades)
    if path.exists():
        if path.read_bytes() != data:
            raise Refused(f"{path} exists with different bytes: the trade export is write-once")
    else:
        write_once(path, data)
    return _sha_bytes(data)


def run_gaps(trades_path: Path, tape_dir: str, out_dir: Path, bot: str, cmd: Sequence[str] = GAPS_CMD, cwd: Path = REPO_ROOT) -> tuple[Path, Path, int]:
    gaps, detail = out_dir / f"{bot}.wis-gaps.json", out_dir / f"{bot}.wis-detail.json"
    argv = [*cmd, "--trades", str(trades_path), "--tape-dir", tape_dir, "--out", str(gaps), "--detail", str(detail)]
    p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)
    if p.returncode != 0:
        tail = (p.stdout.strip().splitlines() or [""])[-1][:200]
        raise Refused(f"arena_worst_gaps exited rc {p.returncode} (must be 0): worst-in-slot not measured; no wis.json. {tail} {p.stderr.strip()[:300]}")
    return gaps, detail, p.returncode


def build(bot: str, reads_dir: str, instant_path: str, ledger_root: str, registry: str, health_texts: Sequence[str], health_shas: Sequence[str], as_of_ms: int,
          restarts_text: str, tape_dir: str, out_dir: str, gaps_cmd: Sequence[str] = GAPS_CMD) -> dict[str, Any]:
    if bot not in F.BOT_IDS:
        raise Refused(f"{bot!r} is not one of the nine HV4 bot_ids")
    rec, rec_sha = load_recorded_pass(reads_dir, bot)
    od = Path(out_dir)
    wis = od / f"{bot}.wis.json"
    if wis.exists():
        raise Refused(f"{wis} exists: the worst-in-slot record is written once")
    if _sha_file(S_DISK.__file__) != ARENA_SCORE_DISK_SHA256:
        raise Refused(f"tools/arena_score.py on disk is not the known c4859323 copy (worst_in_slot_cells must be the reviewed one)")
    trades, rep = reproduce(rec, instant_path, ledger_root, registry, bot, health_texts, health_shas, as_of_ms, restarts_text)
    od.mkdir(parents=True, exist_ok=True)
    tpath = od / f"{bot}.wis-trades.jsonl"
    tsha = write_trades_once(tpath, trades)
    gpath, dpath, rc = run_gaps(tpath, tape_dir, od, bot, gaps_cmd)
    gaps = S_DISK.load_worst_gaps(gpath)
    missing = [r["trade_id"] for r in trades if r["trade_id"] not in gaps]
    if missing:
        raise Refused(f"worst-in-slot not measured: {len(missing)} of {len(trades)} deciding trade(s) have no gap")
    figure = S_DISK.worst_in_slot_cells(trades, gaps, rep["recorded"]["rules"])
    if not figure.get("measured"):
        raise Refused(figure.get("reason", "worst-in-slot not measured"))
    detail = json.loads(dpath.read_text())
    mean, lo = figure["mean_sol"], figure["ci90_lo_sol"]
    out = {"schema": SCHEMA, "bot_id": bot, "report_only": True, "never_changes_the_verdict": True,
           "read_boundary_ms": rep["recorded"]["read_boundary_ms"], "n": len(trades), "stake": rep["recorded"]["stake"], "subcells": rep["recorded"]["subcells"],
           "reproduction": rep["checks"], "recorded_read_sha256": rec_sha, "instant_file_sha256": rep["instant_file_sha256"],
           "trade_export": {"file": tpath.name, "sha256": tsha, "n": len(trades)},
           "gaps": {"file": gpath.name, "sha256": _sha_file(gpath), "arena_worst_gaps_rc": rc, "measured": detail["measured"], "left_out": len(detail["left_out"]),
                    "nonzero": detail["nonzero"], "clipped_negative": detail["clipped_negative"]},
           "worst_in_slot": figure,
           "item6": {"measured": True, "mean_sol_positive": bool(mean is not None and mean > 0), "ci90_lo_sol_positive": bool(lo is not None and lo > 0),
                     "canary_proposal_blocked_by_item6": not (mean is not None and mean > 0),
                     "note": "DEC-031 Am.15 item 6: a worst-in-slot mean <= 0 means no proposal. Report-only. The CI bound is quoted in a proposal (R7), it is not an item 6 test."},
           "credits": {"helius": 0, "rpc": 0, "note": "arena_worst_gaps reads local tape files only"},
           "tools": {"hv4_wis_export_sha256": _sha_file(__file__), "arena_worst_gaps_sha256": _sha_file(REPO_ROOT / "tools" / "arena_worst_gaps.py"),
                     "arena_score_disk_sha256": ARENA_SCORE_DISK_SHA256, "arena_score_pinned_sha256": W.PINNED_ARENA_SCORE_SHA256,
                     "hv4_am15_read_sha256": _sha_file(W.__file__)},
           "disclosure": "Winner's curse and the sibling-selection caveat of DEC-031 Am.15 item 8 apply. No book has cleared the promotion gate."}
    data = (canon(out) + "\n").encode()
    write_once(wis, data)
    out["wis_file"], out["wis_sha256"] = str(wis), _sha_bytes(data)
    return out


def reproduced(rep: dict) -> bool:
    return all(v for k, v in rep.items() if k != "subcells") and all(rep["subcells"].values())


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    ap.add_argument("--bot", required=True, choices=F.BOT_IDS)
    ap.add_argument("--instant", required=True)
    ap.add_argument("--reads-dir", default=S_DISK.READS_DIR)
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ledger-root", default=S_DISK.LEDGER_ROOT)
    ap.add_argument("--registry", default=S_DISK.REGISTRY_PATH)
    ap.add_argument("--health", required=True, action="append")
    ap.add_argument("--health-sha", required=True, action="append")
    ap.add_argument("--restarts", required=True)
    ap.add_argument("--as-of-ms", required=True, type=int)
    a = ap.parse_args(argv)
    try:
        out = build(a.bot, a.reads_dir, a.instant, a.ledger_root, a.registry, [Path(h).read_text() for h in a.health], a.health_sha, a.as_of_ms,
                    Path(a.restarts).read_text(), a.tape_dir, a.out_dir)
    except Exception as ex:  # noqa: BLE001 - any failure is a refusal (exit 2), nothing that looks like a figure is printed
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2
    w = out["worst_in_slot"]
    print(f"WIS {out['bot_id']}  B {out['read_boundary_ms']}  n {out['n']}  reproduced {reproduced(out['reproduction'])}")
    print("subcells " + canon(out["subcells"]))
    print(f"worst-in-slot mean_sol {w['mean_sol']}  ci90_lo_sol {w['ci90_lo_sol']}  worst_cell {w['worst_cell']}  gaps measured {out['gaps']['measured']} nonzero {out['gaps']['nonzero']}")
    print(f"item6 canary_proposal_blocked_by_item6 {out['item6']['canary_proposal_blocked_by_item6']}  credits helius 0 rpc 0")
    print(f"wrote {out['wis_file']} sha256 {out['wis_sha256']}; trades sha256 {out['trade_export']['sha256']}; recorded read sha256 {out['recorded_read_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
