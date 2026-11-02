"""r4bt_decode_cli: the HOST-SIDE RPC wrapper of the r4-b-T decode check (QP-RULING-1102-r4bt Q2 item 4). PAPER ONLY. Read-only RPC. New file (r4bt_*); it edits nothing.

Builds the RPC client (public RPC by default; an optional KEY=VALUE env FILE on the host supplies a Helius URL, read here by `a3_redeploy_check.rpc_url_from_env_file`, never
printed, never in argv, never in an error message), then:

  fetch   --write-name N --write-slot S --out SAMPLE.json   fetch the post-redeploy LOG SAMPLE (the pinned rule: SAMPLE_MIN_TXS txs from S + SAMPLE_START_OFFSET_SLOTS) and
                                                            write the PARITY INPUT {"schema": "r4bt_parity_input_v1", "write", "start_slot", "slots_scanned", "n_txs", "sample"}
  parity  --input SAMPLE.json [--out REPORT.json]           run `tools/r4bt_decode_parity.parity_report` on a parity input, offline (exit 0 PASS, 10 FAIL)
  run     --writes W.json --pins A3PINS --r4bt-pins R4PINS --out RESULT.json [--save-input SAMPLE.json]
                                                            the full decode check (`r4bt_decode_check.check_write` with the parity function as `adapter_check`) for the NEWEST write
                                                            of each program in W.json (the `r4bt_writes` output); deploy_ms = that program's EARLIEST write in the file. RESULT.json is
                                                            `r4bt_decode_check.collect(results)`: the file `tools/r4bt_a3.py classify --decode-checks` reads. Exit 0 whatever the verdict
                                                            (the classifier decides); 11 on a usage / input error.

Files are never overwritten (an existing --out is refused). A --rpc-url containing "helius" is refused (a key in argv); use --rpc-env-file. Output is counts and names only.
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
from tools import r4bt_decode_check as DC
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


def newest_writes(writes: Sequence[dict]) -> list[dict]:
    """[{name, slot, at_ms}] -> per program: the NEWEST write (the sample starts after it) with deploy_ms = that program's EARLIEST write."""
    out: dict[str, dict] = {}
    for w in writes:
        if not (isinstance(w.get("name"), str) and isinstance(w.get("slot"), int) and isinstance(w.get("at_ms"), int)):
            raise CliRefused("a write needs name (str), slot (int) and at_ms (int)")
        cur = out.get(w["name"])
        if cur is None:
            out[w["name"]] = {"name": w["name"], "slot": w["slot"], "deploy_ms": w["at_ms"]}
        else:
            cur["slot"], cur["deploy_ms"] = max(cur["slot"], w["slot"]), min(cur["deploy_ms"], w["at_ms"])
    return [out[k] for k in sorted(out)]


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
    results = []
    for w in newest_writes(writes):
        captured: list[Any] = []

        def adapter(sample: Sequence[Sequence[str]], _c=captured) -> list[str]:
            _c.append(sample)
            return PAR.parity_failures(sample)

        res = DC.check_write({"name": w["name"], "slot": w["slot"]}, pins, r4pins, client=client, http_get=http_get, now_ms=now_ms, deploy_ms=w["deploy_ms"], adapter_check=adapter)
        results.append(res)
        if a.save_input and captured:
            _write_new(Path(a.save_input).with_name(f"{w['name']}-{Path(a.save_input).name}"),
                       {"schema": INPUT_SCHEMA, "write": {"name": w["name"], "slot": w["slot"]}, "n_txs": len(captured[0]), "sample": [list(x) for x in captured[0]]})
    _write_new(a.out, DC.collect(results))
    print(json.dumps({"results": [{"name": r["name"], "slot": r["slot"], "verdict": r["verdict"], "n_reasons": len(r["reasons"])} for r in results]}, sort_keys=True))
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
        cl = client or build_client(a)
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
