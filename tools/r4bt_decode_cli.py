"""r4bt_decode_cli: the HOST-SIDE RPC wrapper of the r4-b-T decode check (QP-RULING-1102-r4bt Q2 item 4). PAPER ONLY. Read-only RPC. New file (r4bt_*); it edits nothing.

Builds the RPC client (public RPC by default; an optional KEY=VALUE env FILE on the host supplies a Helius URL, read here by `a3_redeploy_check.rpc_url_from_env_file`, never
printed, never in argv, never in an error message), then:

  fetch   --write-name N --write-slot S --out SAMPLE.json   fetch the post-redeploy LOG SAMPLE (the pinned rule: SAMPLE_MIN_TXS txs from S + SAMPLE_START_OFFSET_SLOTS) and
                                                            write the PARITY INPUT {"schema": "r4bt_parity_input_v1", "write", "start_slot", "slots_scanned", "n_txs", "sample"}
  parity  --input SAMPLE.json [--out REPORT.json]           run `tools/r4bt_decode_parity.parity_report` on a parity input, offline (exit 0 PASS, 10 FAIL)
  run     --writes W.json --pins A3PINS --r4bt-pins R4PINS --out RESULT.json [--save-input SAMPLE.json]
                                                            the full decode check (`r4bt_decode_evidence.check_all`: decode evidence only, no BOOST terms, QP D1) for EVERY pump / pumpswap
                                                            write in W.json (the `r4bt_writes` output), each on its OWN live interval [slot + 150, next write of the program) (Addendum 1 A);
                                                            every write's deploy_ms = the EARLIEST write in the file. RESULT.json is `collect(results)`, one entry per write: the file
                                                            `tools/r4bt_a3_writes.py classify --decode-checks` reads. Exit 0 whatever the verdict (the classifier decides); 11 on a usage / input error.

Files are never overwritten (an existing --out is refused). A --rpc-url containing "helius" is refused (a key in argv); use --rpc-env-file. Output is counts and names only.

DRY RUN for the manager, after this file is in main and BEFORE the registration record: the parity function on a REAL post-redeploy sample, public RPC, outcome-free (the sample is
public chain log lines; the report is counts and names only; no book data, tape, tip file or decision log is read). One MiScusi job (sh, no bash-isms; about 300 getBlock calls at
0.5 s, so a few minutes; small memory; fast-0 at <= 0.5 CPU or research-0). WRITE_SLOT = the slot of any real past upgrade write of the pump program that is at least 150 slots old
(for example the newest `slot` of the `r4bt_writes` output); the install tree is a clean checkout at the merge commit of this PR; keep the sample file in the job's scratch dir:

    cd "$INSTALL_TREE" && D="$SCRATCH/r4bt-parity-$WRITE_SLOT" && mkdir "$D" && /data/mal/arena/venv-hv4/bin/python -m tools.r4bt_decode_cli fetch --write-name pump --write-slot "$WRITE_SLOT" --out "$D/sample.json" && /data/mal/arena/venv-hv4/bin/python -m tools.r4bt_decode_cli parity --input "$D/sample.json" --out "$D/report.json"

The job exit is 0 on PASS and 10 on FAIL (the report's `failures` names the column or key). A FAIL on a real sample before any redeploy is a finding for QP, not a result to tune around.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from tools import a3_redeploy_check as ARC
from tools import pump_structure_monitor as mon
from tools import r4bt_decode_evidence as DE
from tools import r4bt_decode_parity as PAR

INPUT_SCHEMA = "r4bt_parity_input_v1"


class CliRefused(Exception):
    pass


def add_rpc_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--rpc-url", default=mon.DEFAULT_RPC, help="public RPC (default %(default)s); a URL with a key in it is refused")
    p.add_argument("--rpc-env-file", default=None, help="KEY=VALUE env file on the host holding an RPC URL (e.g. the Helius file on fast-0); read here, never printed")
    p.add_argument("--rpc-var", default=None, help="variable name inside --rpc-env-file (default: the common names, then a bare API key)")


def build_client(a: argparse.Namespace, *, min_interval: float = 0.5, max_calls: int = 450) -> Any:
    if "helius" in str(a.rpc_url).lower() or "api-key" in str(a.rpc_url).lower():
        raise CliRefused("a key-bearing --rpc-url is refused: use --rpc-env-file")
    if a.rpc_env_file:
        try:
            url = ARC.rpc_url_from_env_file(a.rpc_env_file, a.rpc_var)
        except ARC.CheckError as exc:  # the message carries no URL or key
            raise CliRefused(str(exc)) from None
        return mon.RpcClient(url, min_interval=min(min_interval, 0.2), max_calls=max_calls)
    return mon.RpcClient(a.rpc_url, min_interval=min_interval, max_calls=max_calls)


def _write_new(path: str | Path, obj: Any) -> None:
    p = Path(path)
    if p.exists():
        raise CliRefused(f"{p.name} exists: never overwritten")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, sort_keys=True) + "\n", encoding="utf-8")


def _load(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliRefused(f"cannot read {Path(path).name}: {type(exc).__name__}") from None


def cmd_fetch(a: argparse.Namespace, client: Any) -> int:
    start = int(a.write_slot) + ARC.SAMPLE_START_OFFSET_SLOTS
    sample, scanned = ARC.fetch_sample(client, start)
    _write_new(a.out, {"schema": INPUT_SCHEMA, "write": {"name": a.write_name, "slot": int(a.write_slot)}, "start_slot": start, "slots_scanned": scanned, "n_txs": len(sample),
                       "sample": sample})
    print(json.dumps({"fetched": True, "n_txs": len(sample), "slots_scanned": scanned, "start_slot": start}, sort_keys=True))
    return 0


def cmd_parity(a: argparse.Namespace) -> int:
    doc = _load(a.input)
    if not isinstance(doc, dict) or doc.get("schema") != INPUT_SCHEMA or not isinstance(doc.get("sample"), list):
        raise CliRefused("not an r4bt_parity_input_v1 file")
    rep = PAR.parity_report(doc["sample"])
    if a.out:
        _write_new(a.out, rep)
    print(json.dumps(rep, sort_keys=True))
    return 0 if rep["verdict"] == "PASS" else 10


def cmd_run(a: argparse.Namespace, client: Any, http_get: Callable, now_ms: Callable[[], int]) -> int:
    writes = _load(a.writes)
    if not isinstance(writes, list) or not writes:
        raise CliRefused("--writes must be the non-empty JSON list of the r4bt_writes output")
    pins, r4pins = _load(a.pins), _load(a.r4bt_pins)
    captured: list[Any] = []

    def adapter(sample: Sequence[Sequence[str]], population: bool = False) -> list[str]:
        captured.append(sample)
        return PAR.parity_failures(sample, min_txs=0, require_venues=False) if population else PAR.parity_failures(sample)

    try:
        results = DE.check_all(writes, pins, r4pins, client=client, http_get=http_get, now_ms=now_ms, adapter_check=adapter)
    except ValueError as exc:
        raise CliRefused(str(exc)) from None
    if not results:
        raise CliRefused("--writes holds no pump / pumpswap write to check")
    if a.save_input:
        for i, smp in enumerate(captured):
            sp = Path(a.save_input)
            _write_new(sp.with_name(f"{sp.stem}-{i:02d}{sp.suffix}"), {"schema": INPUT_SCHEMA, "n_txs": len(smp), "sample": [list(x) for x in smp]})
    _write_new(a.out, DE.collect(results))
    print(json.dumps({"results": [{"name": r["name"], "slot": r["slot"], "verdict": r["verdict"], "mode": r.get("mode"), "slot_range": r.get("slot_range"),
                                   "n_reasons": len(r["reasons"])} for r in results]}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None, *, client: Any = None, http_get: Callable | None = None, now_ms: Callable[[], int] | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b-T decode check: host-side RPC wrapper.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--write-name", required=True)
    f.add_argument("--write-slot", required=True, type=int)
    f.add_argument("--out", required=True)
    add_rpc_args(f)
    q = sub.add_parser("parity")
    q.add_argument("--input", required=True)
    q.add_argument("--out", default=None)
    r = sub.add_parser("run")
    r.add_argument("--writes", required=True)
    r.add_argument("--pins", required=True, help="tools/a3_redeploy_pins.json")
    r.add_argument("--r4bt-pins", required=True, help='JSON {"frozen_sha", "frozen_blobs"} of W')
    r.add_argument("--out", required=True)
    r.add_argument("--save-input", default=None)
    add_rpc_args(r)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "parity":
            return cmd_parity(a)
        cl = client or build_client(a, max_calls=4500 if a.cmd == "run" else 450)  # `run` checks every write: up to ~300 getBlock + the migrate paging each
        if a.cmd == "fetch":
            return cmd_fetch(a, cl)
        return cmd_run(a, cl, http_get or mon.urllib_get, now_ms or (lambda: int(time.time() * 1000)))
    except (CliRefused, ARC.CheckError) as exc:
        print(f"r4bt_decode_cli: REFUSED: {exc}", file=sys.stderr)
        return 11
    except Exception as exc:  # noqa: BLE001 - an RPC failure: class name only (a URL or key can sit in the message)
        print(f"r4bt_decode_cli: ERROR {type(exc).__name__}", file=sys.stderr)
        return 11


if __name__ == "__main__":
    sys.exit(main())
