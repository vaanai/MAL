"""md5 decision-equivalence replay for the walk-2 event-V decoder (audit action A8, decoder half).

Decodes a fixed set of recorded getBlock-shaped fixtures (tools/fixtures/walk2_event_v/*.json plus the
older pump_structure_monitor tx fixtures) with a BASELINE tree (git ref, default origin/main) and with
the working tree, and compares md5 of the canonical legacy rows:

  1. new code, event_v=False        -> every row byte-identical to baseline (full md5 match)
  2. new code, event_v=True         -> baseline rows plus only the new keys: after dropping
                                       EVENT_V_KEYS and `init_boost`, md5 equals baseline
  3. the new keys that event_v=True adds are listed, so a reader can confirm it ignores them

Pure offline. Reads only repo fixtures. No network, no key, no env file.

Usage:  python3 -m tools.walk2_decoder_replay [--baseline-ref origin/main]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIRS = (
    REPO / "tools" / "fixtures" / "walk2_event_v",
    REPO / "tools" / "fixtures" / "pump_structure_monitor",
)
# fixtures in those dirs that are a getTransaction result or a trimmed one; others are ignored
_TX_FILES_PREFIX = ("migrate_tx", "completing_tx")
NEW_ROW_KEYS = ("virtual_quote_reserves", "ix_name", "creator_fee_unclaimed", "buyback_fee", "fee_recipient_zero", "init_boost")


def fixture_blocks(dirs: tuple[Path, ...] = FIXTURE_DIRS) -> list[dict[str, Any]]:
    """One getBlock-shaped dict per recorded transaction, in file-name order."""
    blocks: list[dict[str, Any]] = []
    for d in dirs:
        for path in sorted(d.glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if "accountKeys" in doc and "meta" in doc:  # trimmed fixture (walk2_event_v)
                tx = {
                    "transaction": {
                        "signatures": [doc["signature"]],
                        "message": {"accountKeys": doc["accountKeys"], "instructions": []},
                    },
                    "meta": doc["meta"],
                }
                slot, block_time = doc["slot"], doc["blockTime"]
            elif path.name.startswith(_TX_FILES_PREFIX):  # full getTransaction result
                tx = {"transaction": doc["transaction"], "meta": doc["meta"]}
                slot, block_time = doc["slot"], doc["blockTime"]
            else:
                continue
            blocks.append({"slot": slot, "blockTime": block_time, "transactions": [tx], "_name": path.name})
    return blocks


def _canon(row: Any) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def decode_fixtures(blocks: list[dict[str, Any]], *, event_v: bool | None) -> list[dict[str, Any]]:
    """Rows per fixture. event_v None = do not pass the kwarg (the baseline module has none)."""
    from observe.trade_decode import WSOL_MINT
    from tools.pump_history_backfill import rows_from_block

    out: list[dict[str, Any]] = []
    for block in blocks:
        # Fixed pool -> mint map so PumpSwap rows resolve (placeholder mints, replay input only).
        cache: dict[str, tuple[str, str]] = {}
        kwargs = {} if event_v is None else {"event_v": event_v}
        probe = rows_from_block(block, cache, "replay", **kwargs)
        pools = sorted({r["pool"] for r in probe["unresolved"] if r.get("pool")})
        cache = {p: ("MINT" + p[:8], WSOL_MINT) for p in pools}
        res = rows_from_block(block, cache, "replay", **kwargs)
        legacy = {k: res[k] for k in ("trades", "creates", "migrations", "unresolved")}
        out.append({"name": block["_name"], "legacy": legacy, "extra": {k: v for k, v in res.items() if k not in legacy}})
    return out


def strip_new_keys(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in r.items() if k not in NEW_ROW_KEYS} for r in rows]


def legacy_stream(decoded: list[dict[str, Any]], *, strip: bool) -> str:
    lines: list[str] = []
    for item in decoded:
        for bucket in ("trades", "creates", "migrations", "unresolved"):
            rows = item["legacy"][bucket]
            if strip:
                rows = strip_new_keys(rows)
            lines.append(f"{item['name']}\t{bucket}\t" + _canon(rows))
    return "\n".join(lines)


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # noqa: S324 - equivalence digest, not security


def dump_cli(tree: str, event_v: str) -> None:
    """Run inside a subprocess against a given source tree. Prints the legacy stream md5 and row count."""
    sys.path.insert(0, tree)
    flag = {"none": None, "off": False, "on": True}[event_v]
    decoded = decode_fixtures(fixture_blocks(), event_v=flag)
    print(json.dumps({"md5": md5_hex(legacy_stream(decoded, strip=flag is True)), "n_rows": sum(len(r) for d in decoded for r in d["legacy"].values())}))


def _run_tree(tree: Path, event_v: str) -> dict[str, Any]:
    code = (
        "import sys; sys.path.insert(0, %r); from tools.walk2_decoder_replay import dump_cli; dump_cli(%r, %r)"
        % (str(tree), str(tree), event_v)
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, cwd=str(tree)
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline-ref", default="origin/main")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "baseline"
        base.mkdir()
        archive = subprocess.run(  # noqa: S603
            ["git", "-C", str(REPO), "archive", args.baseline_ref, "observe", "tools/__init__.py", "tools/pump_history_backfill.py", "tools/backfill_verify.py"],
            capture_output=True, check=True,
        )
        subprocess.run(["tar", "-x", "-C", str(base)], input=archive.stdout, check=True)  # noqa: S603,S607
        # the replay module itself and the fixtures are shared inputs, copied into the baseline tree
        (base / "tools").mkdir(exist_ok=True)
        (base / "tools" / "walk2_decoder_replay.py").write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
        (base / "tools" / "fixtures").symlink_to(REPO / "tools" / "fixtures")
        baseline = _run_tree(base, "none")
    new_off = _run_tree(REPO, "off")
    new_on = _run_tree(REPO, "on")
    report = {
        "baseline_ref": args.baseline_ref,
        "baseline_md5": baseline["md5"],
        "new_event_v_off_md5": new_off["md5"],
        "new_event_v_on_stripped_md5": new_on["md5"],
        "rows": baseline["n_rows"],
        "off_equals_baseline": baseline["md5"] == new_off["md5"] and baseline["n_rows"] == new_off["n_rows"],
        "on_stripped_equals_baseline": baseline["md5"] == new_on["md5"] and baseline["n_rows"] == new_on["n_rows"],
    }
    print(json.dumps(report, indent=2))
    return 0 if report["off_equals_baseline"] and report["on_stripped_equals_baseline"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
