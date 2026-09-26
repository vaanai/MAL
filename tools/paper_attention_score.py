#!/usr/bin/env python3
"""Score attention first-seen entries on the PR #76 paper tape simulator.

Needs tools.paper_tape_scoreboard (branch cursor/paper-tape-score-093c).
Does not write into the trade tape directory. Paper only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from observe.attention import (
    ATTENTION_FILE_RE,
    HttpJson,
    default_sources,
    parse_dex_orders,
    stored_attention,
)
from observe.trade_store import hour_stamp

try:
    from tools.paper_curve_math import DEFAULT_SIZE_LAMPORTS, DEFAULT_SLIPPAGE_CAP, LAMPORTS_PER_SOL
    from tools.paper_price_path import CreateSignal, MintPath, load_creates, resolve_sealed_path, stream_paths
    from tools.paper_tape_scoreboard import (
        EXIT_RULES,
        HEADLINE_LATENCY,
        aggregate_rule,
        simulate_book,
    )
except ImportError as exc:  # pragma: no cover - host run uses PR #76 on PYTHONPATH
    EXIT_RULES = ()  # type: ignore[assignment]
    _IMPORT_ERROR = exc

    def resolve_sealed_path(path: Path) -> Path | None:
        try:
            if path.is_file() and not path.is_symlink():
                return path
        except OSError:
            pass
        name = path.name
        if name.endswith(".jsonl.zst") or not name.endswith(".jsonl"):
            return None
        sibling = Path(str(path) + ".zst")
        try:
            if sibling.is_file() and not sibling.is_symlink():
                return sibling
        except OSError:
            return None
        return None
else:
    _IMPORT_ERROR = None

DEX_ORDERS = "https://api.dexscreener.com/orders/v1/solana/{mint}"


def _require_sim() -> None:
    if _IMPORT_ERROR is not None:
        raise SystemExit(
            "paper_attention_score needs PR #76 modules "
            f"(tools.paper_tape_scoreboard): {_IMPORT_ERROR}"
        )


def _jsonl_text(path: Path) -> str:
    if path.name.endswith(".jsonl.zst"):
        proc = subprocess.run(
            ["zstd", "-d", "-c", "-q", str(path)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return proc.stdout.decode("utf-8", errors="replace")
    return path.read_text(encoding="utf-8", errors="replace")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    target = resolve_sealed_path(path)
    if target is None:
        return
    try:
        text = _jsonl_text(target)
    except (FileNotFoundError, subprocess.CalledProcessError):
        target = resolve_sealed_path(path)
        if target is None:
            return
        try:
            text = _jsonl_text(target)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def list_attention_files(path: Path, *, now: datetime | None = None) -> list[Path]:
    """Sealed hourly attention-YYYY-MM-DDTHH.jsonl.zst only. Skip the open hour."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    open_stamp = hour_stamp(now or datetime.now(timezone.utc))
    out: list[Path] = []
    for p in path.iterdir():
        if not p.is_file() or p.is_symlink():
            continue
        if not ATTENTION_FILE_RE.fullmatch(p.name) or not p.name.endswith(".zst"):
            continue
        stamp = p.name[len("attention-") : -len(".jsonl.zst")]
        if stamp == open_stamp:
            continue
        out.append(p)
    return sorted(out)


def load_attention_rows(paths: Sequence[Path], *, now: datetime | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if path.is_dir():
            for f in list_attention_files(path, now=now):
                rows.extend(iter_jsonl(f))
        elif path.is_file():
            rows.extend(iter_jsonl(path))
    return rows


def earliest_by_kind(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """kind -> mint -> row with smallest t_first_ms."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        kind = row.get("kind")
        mint = row.get("mint")
        t_ms = row.get("t_first_ms")
        if not isinstance(kind, str) or not isinstance(mint, str) or not isinstance(t_ms, int):
            continue
        bucket = out.setdefault(kind, {})
        prev = bucket.get(mint)
        if prev is None or int(prev["t_first_ms"]) > t_ms:
            bucket[mint] = row
    return out


def merge_kinds(by_kind: dict[str, dict[str, dict[str, Any]]], kinds: Sequence[str]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for kind in kinds:
        for mint, row in (by_kind.get(kind) or {}).items():
            prev = merged.get(mint)
            if prev is None or int(prev["t_first_ms"]) > int(row["t_first_ms"]):
                merged[mint] = row
    return merged


def snapshot_now(*, check_orders: bool = True, order_limit: int = 40) -> list[dict[str, Any]]:
    """One-shot lists. t_first_ms is now (late vs a live poller). paid_at_ms is a feature only."""
    http = HttpJson()
    t_seen = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    order_mints: list[str] = []
    for spec in default_sources():
        try:
            payload = http.get(spec.url, spec.headers)
        except Exception as exc:
            print(f"snapshot_skip source={spec.name} err={exc}", file=sys.stderr)
            continue
        for cand in spec.parse(payload):
            key = (cand.kind, cand.mint)
            if key in seen:
                continue
            seen.add(key)
            rec = stored_attention(cand, t_seen, t_seen)
            rec["snapshot"] = True
            rows.append(rec)
            if cand.kind in ("dex_profile", "dex_boost", "dex_ad"):
                order_mints.append(cand.mint)
    if check_orders:
        uniq: list[str] = []
        for mint in order_mints:
            if mint not in uniq:
                uniq.append(mint)
        for mint in uniq[:order_limit]:
            try:
                payload = http.get(DEX_ORDERS.format(mint=mint), None)
            except Exception as exc:
                print(f"snapshot_orders_skip mint={mint[:8]} err={exc}", file=sys.stderr)
                continue
            for cand in parse_dex_orders(payload, mint):
                key = (cand.kind, cand.mint)
                if key in seen:
                    continue
                seen.add(key)
                rec = stored_attention(cand, t_seen, t_seen)
                rec["snapshot"] = True
                if cand.paid_at_ms is not None:
                    rec["paid_at_feature"] = True
                rows.append(rec)
    return rows


def _stub_create(mint: str, t_ms: int) -> CreateSignal:
    return CreateSignal(
        mint=mint,
        t_signal_ms=t_ms,
        creator=None,
        signature=None,
        v_sol=None,
        v_token_ui=None,
        mcap_sol=None,
        initial_buy_ui=None,
        sol_amount=None,
    )


def bind_signal(path: MintPath, t_signal_ms: int) -> MintPath:
    return MintPath(create=replace(path.create, t_signal_ms=t_signal_ms), prints=path.prints)


def causal_entry_ms(t_first_ms: int, first_print_ms: int | None) -> int:
    """Buy signal before +latency: our first-seen, never before the first tape print.

    Vendor stamps (Dex paymentTimestamp, stream start, pool_created_at) are lag
    features only. They are not an entry clock.
    """
    if first_print_ms is None:
        return int(t_first_ms)
    return max(int(t_first_ms), int(first_print_ms))


def first_print_ms(path: Any) -> int | None:
    prints = getattr(path, "prints", None) or ()
    if not prints:
        return None
    t_ms = getattr(prints[0], "t_recv_ms", None)
    return int(t_ms) if isinstance(t_ms, int) else None


def bind_attention_signal(path: MintPath, t_first_ms: int) -> MintPath:
    return bind_signal(path, causal_entry_ms(t_first_ms, first_print_ms(path)))


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"


def _fmt_rate(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.1f}%"


def summarize_book(name: str, labels: Sequence[dict[str, Any]], size_lamports: int) -> dict[str, Any]:
    hold = [row for row in labels if row.get("exit_rule") == "hold_30s" and row.get("latency_s") == HEADLINE_LATENCY]
    grid = {}
    for rule in EXIT_RULES:
        rows = [
            row
            for row in labels
            if row.get("exit_rule") == rule.rule_id and row.get("latency_s") == HEADLINE_LATENCY
        ]
        grid[rule.rule_id] = aggregate_rule(rows, size_lamports=size_lamports, fail_rates=(0.0,))
    hold_stats = aggregate_rule(hold, size_lamports=size_lamports, fail_rates=(0.0,))
    return {"book": name, "hold_30s": hold_stats, "by_exit": grid}


def lag_rows(
    book: dict[str, dict[str, Any]],
    paths: dict[str, MintPath],
) -> dict[str, Any]:
    lags_create: list[int] = []
    lags_print: list[int] = []
    before_create = 0
    before_print = 0
    missing_path = 0
    for mint, row in book.items():
        t_first = int(row["t_first_ms"])
        path = paths.get(mint)
        if path is None:
            missing_path += 1
            continue
        lags_create.append(t_first - path.create.t_signal_ms)
        if t_first < path.create.t_signal_ms:
            before_create += 1
        if path.prints:
            first_print = path.prints[0].t_recv_ms
            lags_print.append(t_first - first_print)
            if t_first < first_print:
                before_print += 1
    def _pct(vals: list[int], p: float) -> float | None:
        if not vals:
            return None
        ordered = sorted(vals)
        k = (len(ordered) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(ordered) - 1)
        w = k - lo
        return ordered[lo] * (1.0 - w) + ordered[hi] * w

    return {
        "n_with_path": len(book) - missing_path,
        "n_missing_path": missing_path,
        "lag_vs_create_ms": {
            "n": len(lags_create),
            "median": _pct(lags_create, 0.5),
            "p10": _pct(lags_create, 0.1),
            "p90": _pct(lags_create, 0.9),
            "before_create_n": before_create,
        },
        "lag_vs_first_print_ms": {
            "n": len(lags_print),
            "median": _pct(lags_print, 0.5),
            "p10": _pct(lags_print, 0.1),
            "p90": _pct(lags_print, 0.9),
            "before_print_n": before_print,
        },
    }


def run_score(
    *,
    tape: Sequence[Path],
    creates: Sequence[Path],
    attention_rows: Sequence[dict[str, Any]],
    output_dir: Path,
    size_lamports: int,
    slippage_cap: float,
) -> dict[str, Any]:
    _require_sim()
    create_map = load_creates(creates)
    by_kind = earliest_by_kind(attention_rows)
    books_spec = {
        "dex_paid_profile": merge_kinds(by_kind, ("dex_profile", "dex_paid_profile")),
        "dex_boost": merge_kinds(by_kind, ("dex_boost",)),
        "dex_ad": merge_kinds(by_kind, ("dex_ad", "dex_paid_tokenAd")),
        "pump_koth": merge_kinds(by_kind, ("pump_koth",)),
        "pump_live": merge_kinds(by_kind, ("pump_live",)),
        "pump_featured": merge_kinds(by_kind, ("pump_featured", "pump_graduating")),
        "gecko_trending": merge_kinds(by_kind, ("gecko_trending",)),
        "dex_paid_any": merge_kinds(
            by_kind, ("dex_profile", "dex_boost", "dex_ad", "dex_paid_profile", "dex_paid_tokenAd")
        ),
        "koth_or_live": merge_kinds(by_kind, ("pump_koth", "pump_live")),
    }
    for book in books_spec.values():
        for mint, row in book.items():
            if mint not in create_map:
                create_map[mint] = _stub_create(mint, int(row["t_first_ms"]))
    paths, stats = stream_paths(create_map, tape)
    if stats.t_min_ms is None or stats.t_max_ms is None:
        raise SystemExit("tape has no t_recv_ms")

    # Buy-all baseline: real observe creates inside the tape window, original T.
    baseline_paths = [
        path
        for path in paths.values()
        if path.create.signature is not None or path.create.v_sol is not None
    ]
    # Stubs have no creator payload; prefer observe-backed creates only.
    observe_paths = [
        p for p in paths.values() if p.create.signature is not None or p.create.v_sol is not None
    ]
    if not observe_paths:
        observe_paths = baseline_paths
    windowed = [
        p
        for p in observe_paths
        if stats.t_min_ms - 2_000 <= p.create.t_signal_ms <= stats.t_max_ms
    ]
    print(
        f"creates_loaded={len(create_map)} observe_in_window={len(windowed)} "
        f"kept_prints={stats.kept} tape_lines={stats.lines} attention_rows={len(attention_rows)}",
        file=sys.stderr,
    )
    base_labels = simulate_book(
        windowed,
        latencies=(HEADLINE_LATENCY,),
        tape_end_ms=stats.t_max_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
    )
    result_books: dict[str, Any] = {
        "buy_every_create": summarize_book("buy_every_create", base_labels, size_lamports)
    }
    lags: dict[str, Any] = {}
    for name, mint_map in books_spec.items():
        bound: list[MintPath] = []
        used = 0
        for mint, row in mint_map.items():
            path = paths.get(mint)
            if path is None:
                continue
            bound.append(bind_attention_signal(path, int(row["t_first_ms"])))
            used += 1
        labels = simulate_book(
            bound,
            latencies=(HEADLINE_LATENCY,),
            tape_end_ms=stats.t_max_ms,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
        )
        summary = summarize_book(name, labels, size_lamports)
        summary["signals"] = len(mint_map)
        summary["paths"] = used
        result_books[name] = summary
        lags[name] = lag_rows(mint_map, paths)

    board = {
        "schema": "paper_attention_score_v1",
        "window": {
            "tape_start_ms": stats.t_min_ms,
            "tape_end_ms": stats.t_max_ms,
            "tape_start": datetime.fromtimestamp(stats.t_min_ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "tape_end": datetime.fromtimestamp(stats.t_max_ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "creates_in_window": len(windowed),
            "size_lamports": size_lamports,
            "slippage_cap": slippage_cap,
            "headline_latency_s": HEADLINE_LATENCY,
        },
        "scan": stats.as_dict(),
        "attention_n": len(attention_rows),
        "books": result_books,
        "timing": lags,
        "caveats": [
            "Poller first-seen is when this process observed the list, not when Dex/pump first showed it.",
            "Entry is max(t_first_ms, first tape print) + 1s. Never Dex paymentTimestamp or other vendor stamps.",
            "pump.fun GET /coins/king-of-the-hill is 404; pump_koth is hot-coin plus graduating rank 0.",
            "n is one 0.05 SOL fill per mint. Totals are not a bankroll.",
            "One UTC day of tape. Rugs kept. Real fees. Same simulator as PR #76.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scoreboard.json").write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")
    (output_dir / "scoreboard.md").write_text(format_attention_md(board), encoding="utf-8")
    return board


def format_attention_md(board: dict[str, Any]) -> str:
    win = board.get("window") or {}
    lines = [
        "# Attention first-seen vs buy-every-create",
        "",
        f"Window {win.get('tape_start')}–{win.get('tape_end')}. "
        f"Buy at signal+{win.get('headline_latency_s')}s, size "
        f"{int(win.get('size_lamports') or 0) / LAMPORTS_PER_SOL:.2f} SOL, fail 0, rugs kept.",
        "",
        "| book | signals | paths | n | median | mean | win | total SOL | no-exit | miss |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, book in (board.get("books") or {}).items():
        stats = book.get("hold_30s") or {}
        lines.append(
            "| {name} | {sig} | {paths} | {n} | {med} | {mean} | {win} | {total} | {rug} | {miss} |".format(
                name=name,
                sig=book.get("signals", ""),
                paths=book.get("paths", ""),
                n=stats.get("n"),
                med=_fmt(stats.get("median_sol")),
                mean=_fmt(stats.get("mean_sol")),
                win=_fmt_rate(stats.get("win_rate")),
                total=_fmt(stats.get("total_sol")),
                rug=stats.get("no_exit_n"),
                miss=stats.get("miss_n"),
            )
        )
    lines.append("")
    lines.append("## Timing vs on-chain (first-seen minus create / first print)")
    lines.append("")
    lines.append("| book | n path | lag create median ms | before create | lag print median ms | before print | missing path |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name, lag in (board.get("timing") or {}).items():
        c = lag.get("lag_vs_create_ms") or {}
        p = lag.get("lag_vs_first_print_ms") or {}
        lines.append(
            f"| {name} | {lag.get('n_with_path')} | {_fmt(c.get('median'))} | {c.get('before_create_n')} | "
            f"{_fmt(p.get('median'))} | {p.get('before_print_n')} | {lag.get('n_missing_path')} |"
        )
    lines.append("")
    for note in board.get("caveats") or []:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def _looks_like_tape_dir(path: Path) -> bool:
    text = str(path)
    return "/sealed/trades" in text or text.rstrip("/").endswith("/sealed/trades")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score attention first-seen through the PR #76 paper simulator")
    parser.add_argument("--tape", nargs="+", type=Path, required=True)
    parser.add_argument("--creates", nargs="+", type=Path, required=True)
    parser.add_argument("--attention", nargs="*", type=Path, default=[])
    parser.add_argument("--snapshot", action="store_true", help="also poll live lists once (t_first=now; paid_at is a feature)")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--size-sol", type=float, default=0.05)
    args = parser.parse_args(argv)
    if _looks_like_tape_dir(args.output_dir):
        raise SystemExit("refusing to write into the trade tape directory")
    if args.size_sol <= 0:
        raise SystemExit("size-sol must be positive")
    rows: list[dict[str, Any]] = []
    if args.attention:
        rows.extend(load_attention_rows(args.attention))
    if args.snapshot:
        snap = snapshot_now()
        print(f"snapshot_rows={len(snap)}", file=sys.stderr)
        rows.extend(snap)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        snap_path = args.output_dir / "snapshot.jsonl"
        with snap_path.open("w", encoding="utf-8") as fh:
            for rec in snap:
                fh.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
    if not rows:
        raise SystemExit("no attention rows (pass --attention and/or --snapshot)")
    size_lamports = int(round(args.size_sol * LAMPORTS_PER_SOL)) if _IMPORT_ERROR is None else 50_000_000
    board = run_score(
        tape=args.tape,
        creates=args.creates,
        attention_rows=rows,
        output_dir=args.output_dir,
        size_lamports=size_lamports,
        slippage_cap=DEFAULT_SLIPPAGE_CAP if _IMPORT_ERROR is None else 0.15,
    )
    buy = ((board.get("books") or {}).get("buy_every_create") or {}).get("hold_30s") or {}
    print(
        f"buy_every_create hold_30s n={buy.get('n')} median={_fmt(buy.get('median_sol'))} "
        f"total={_fmt(buy.get('total_sol'))}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
