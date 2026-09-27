#!/usr/bin/env python3
"""Frozen-book nightly scoreboard. Paper only.

Until 2026-10-05 the 04:15 job does not refit the exploratory walk-forward
and does not rewrite entry_model.txt, barrier_hit_100_30.txt, or
mig15_model.txt. Those files are what the forward books reload.

It does:
- fit the frozen boosters on live tape at or before the freeze (bounded),
- append the forward holdout for sealed hours after the freeze,
- append the backward holdout for sealed backfill hours at or before
  2026-09-25T06:58Z, using those frozen fits.

Each append loads at most MAL_FROZEN_CHUNK_BYTES of compressed tape
(default 1 GiB) plus one prior hour for print context, then drops the books.
"""

from __future__ import annotations

import gc
import json
import os
import pickle
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.laya_backfill_holdout import (
    CREATE_DRAW_SEED,
    DEPLOY_RULE_ID,
    HOLDOUT_END_ISO,
    MIG15_FRACTION,
    MIG15_ID,
    TRAIN_TAPE_PAD_MS,
    LagDraw,
    _fit_frozen,
    _hour_start_ms,
    _release_books,
    _score_mig15,
    continue_take,
    day_status,
    decision_hour_key,
    fit_mig15,
    live_train_tape,
    load_backfill_creates,
    sealed_holdout_hours,
    write_backward_report,
)
from tools.laya_v0 import (
    CANDIDATE_FREEZE_AT,
    CANDIDATE_FREEZE_MS,
    FEATURE_NAMES,
    FROZEN_CANDIDATES,
    LATENCY_DRAW_SEED,
    PROMOTION_RULE,
    BookTrade,
    Booster,
    DecisionRow,
    RankWindow,
    _md_frozen,
    available_backend,
    book_stats,
    build_dataset,
    combine_fail_models,
    fit_headline_curves,
    load_books,
    yield_for_runner,
    load_latency_report,
    recv_to_decision_hop_ms,
    stamp_pressure_pnls,
)
from tools.funding_graph import FundingGraph
from tools.graduated_swing import load_attention
from tools.paper_price_path import load_creates
from tools.paper_tape_scoreboard import DEFAULT_SLIPPAGE_CAP, filter_window

REVIEW_ON = "2026-10-05"
CHUNK_BYTES_DEFAULT = 1_000_000_000
FORWARD_STATE = "frozen_forward_state.json"
FORWARD_TAKES = "frozen_forward_takes.jsonl"
BACKWARD_STATE = "frozen_backward_state.json"
BACKWARD_TAKES = "frozen_backward_takes.jsonl"
FIT_DIR = "frozen-fit"
# Deploy files the forward runner reloads. This job must not rewrite them.
DEPLOY_NAMES = ("entry_model.txt", "barrier_hit_100_30.txt")
MIG15_DEPLOY = Path("/var/lib/mal/paper/graduated-swing/out/mig15_model.txt")
MIG15_DEPLOY_META = Path("/var/lib/mal/paper/graduated-swing/out/mig15_model.json")


def nightly_mode(today: str | None = None) -> str:
    """`frozen` until the review date, then the exploratory retrain may run again."""
    day = today or time.strftime("%Y-%m-%d", time.gmtime())
    return "exploratory" if day >= REVIEW_ON else "frozen"


def chunk_bytes() -> int:
    raw = os.environ.get("MAL_FROZEN_CHUNK_BYTES", "")
    if raw.strip().isdigit():
        return max(1, int(raw))
    return CHUNK_BYTES_DEFAULT


def prefer_sealed(paths: Sequence[Path]) -> list[Path]:
    """One file per hour. A sealed .zst wins over a raw .jsonl of the same hour."""
    chosen: dict[str, Path] = {}
    loose: list[Path] = []
    for path in paths:
        start = _hour_start_ms(path.name)
        if start is None:
            loose.append(path)
            continue
        key = path.name[path.name.index("trades-") + len("trades-") : path.name.index("trades-") + len("trades-") + 13]
        prev = chosen.get(key)
        if prev is None or (path.name.endswith(".jsonl.zst") and not prev.name.endswith(".jsonl.zst")):
            chosen[key] = path
    return sorted(chosen.values(), key=lambda p: p.name) + sorted(loose, key=lambda p: p.name)


def chunk_by_bytes(paths: Sequence[Path], max_bytes: int) -> list[list[Path]]:
    """Groups of files whose sizes sum to at most max_bytes. A larger file stays alone."""
    groups: list[list[Path]] = []
    current: list[Path] = []
    size = 0
    for path in paths:
        try:
            n = path.stat().st_size
        except OSError:
            n = 0
        if current and size + n > max_bytes:
            groups.append(current)
            current = []
            size = 0
        current.append(path)
        size += n
    if current:
        groups.append(current)
    return groups


def _rss_mib() -> int:
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        return 0
    return 0


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _atomic_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


class TakeLedger:
    """Hours already walked, the rank-window tail, and the taken trades."""

    def __init__(self, state_path: Path, takes_path: Path) -> None:
        self.state_path = state_path
        self.takes_path = takes_path
        state = _read_json(state_path)
        hours = state.get("hours") if isinstance(state.get("hours"), list) else []
        self.hours = [str(item) for item in hours]
        windows = state.get("windows") if isinstance(state.get("windows"), dict) else {}
        self.windows = {str(k): [float(v) for v in vals] for k, vals in windows.items() if isinstance(vals, list)}
        pools = state.get("pool_n") if isinstance(state.get("pool_n"), dict) else {}
        self.pool_n = {str(k): int(v) for k, v in pools.items() if isinstance(v, int)}
        self.rows_scored = int(state.get("rows_scored") or 0)
        self.takes = _read_jsonl(takes_path)
        self._hour_set = set(self.hours)

    def has(self, hour: str) -> bool:
        return hour in self._hour_set

    def window(self, spec_id: str, fraction: float) -> RankWindow:
        rank = RankWindow(fraction)
        rank.scores = list(self.windows.get(spec_id) or [])
        return rank

    def add_chunk(
        self,
        hours: Sequence[str],
        takes: Sequence[dict[str, Any]],
        windows: dict[str, RankWindow],
        pool_n: dict[str, int],
        rows_scored: int,
    ) -> None:
        for hour in hours:
            if hour not in self._hour_set:
                self.hours.append(hour)
                self._hour_set.add(hour)
        self.takes.extend(takes)
        for spec_id, rank in windows.items():
            self.windows[spec_id] = list(rank.scores)
        for spec_id, count in pool_n.items():
            self.pool_n[spec_id] = self.pool_n.get(spec_id, 0) + int(count)
        self.rows_scored += int(rows_scored)
        self.save()

    def save(self) -> None:
        _atomic_json(
            self.state_path,
            {
                "schema": "laya_frozen_ledger_v1",
                "hours": self.hours,
                "windows": self.windows,
                "pool_n": self.pool_n,
                "rows_scored": self.rows_scored,
            },
        )
        _atomic_jsonl(self.takes_path, self.takes)


def _hour_token(name: str) -> str | None:
    marker = "trades-"
    if marker not in name:
        return None
    token = name[name.index(marker) + len(marker) : name.index(marker) + len(marker) + 13]
    if len(token) != 13 or token[10] != "T":
        return None
    return token


def sealed_live_hours(tape_dir: Path, *, after_ms: int, now: datetime | None = None) -> list[Path]:
    """Sealed hourly tapes whose hour ends after `after_ms`. Skips the open hour."""
    if not tape_dir.is_dir():
        return []
    open_stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
    found: list[Path] = []
    for path in tape_dir.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        if not path.name.endswith(".jsonl.zst"):
            continue
        token = _hour_token(path.name)
        if token is None or token == open_stamp:
            continue
        start = _hour_start_ms(path.name)
        if start is None or start + 3_600_000 <= after_ms:
            continue
        found.append(path)
    return sorted(found, key=lambda p: p.name)


def _file_mtimes(paths: Sequence[Path]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for path in paths:
        try:
            out[str(path)] = path.stat().st_mtime_ns if path.is_file() else None
        except OSError:
            out[str(path)] = None
    return out


def _guarded_paths(output_dir: Path) -> list[Path]:
    paths = [output_dir / name for name in DEPLOY_NAMES]
    paths.extend([MIG15_DEPLOY, MIG15_DEPLOY_META])
    return paths


def _preserve_once(path: Path) -> None:
    backup = path.with_name(path.name + ".before-frozen-nightly")
    if path.is_file() and not backup.is_file():
        backup.write_bytes(path.read_bytes())


def _deploy_block(output_dir: Path) -> dict[str, Any]:
    entry = _read_json(output_dir / "scoreboard.json").get("entry")
    if not isinstance(entry, dict):
        return {}
    deploy = entry.get("deploy")
    return dict(deploy) if isinstance(deploy, dict) else {}


def _barrier_block(output_dir: Path) -> dict[str, Any]:
    entry = _read_json(output_dir / "scoreboard.json").get("entry")
    if not isinstance(entry, dict):
        return {}
    block = entry.get("barrier_deploy")
    return dict(block) if isinstance(block, dict) else {}


def _compact(spec_id: str, rule: str, row: DecisionRow) -> dict[str, Any]:
    pnl = row.pnl_by_rule.get(rule)
    p1 = row.pnl_pressure_1.get(rule)
    p2 = row.pnl_pressure_2.get(rule)
    return {
        "id": spec_id,
        "mint": row.mint,
        "t_ms": int(row.decision_t_ms),
        "rule": rule,
        "pnl": int(pnl) if isinstance(pnl, int) else None,
        "p1": int(p1) if isinstance(p1, int) else None,
        "p2": int(p2) if isinstance(p2, int) else None,
    }


def _mig_records(rows: Sequence[DecisionRow], curve_1: Any, curve_2: Any) -> list[dict[str, Any]]:
    """Per-trade mig15 numbers. Same mix `_mig_gated` applies later, stored once."""
    from tools.graduated_swing import flat_mix
    from tools.paper_fail_pressure import Attempt, Pressure, headline_pnl

    out: list[dict[str, Any]] = []
    for row in rows:
        raw = row.pnl_by_rule.get(DEPLOY_RULE_ID)
        if not isinstance(raw, int):
            continue
        pressure = getattr(row, "pressure", None) or Pressure(0, 0)
        attempt = Attempt(
            entry_status=row.entry_status,
            exit_status=row.exit_status_by_rule.get(DEPLOY_RULE_ID, ""),
            pnl_lamports=raw,
            pressure=pressure,
        )
        p1 = None if curve_1 is None else headline_pnl(attempt, curve_1)
        p2 = None if curve_2 is None else headline_pnl(attempt, curve_2)
        out.append(
            {
                "id": MIG15_ID,
                "mint": row.mint,
                "t_ms": int(row.decision_t_ms),
                "rule": DEPLOY_RULE_ID,
                "pnl": flat_mix(raw, row.entry_status),
                "p1": int(p1) if isinstance(p1, int) else None,
                "p2": int(p2) if isinstance(p2, int) else None,
            }
        )
    return out


def _candidate_stats(spec: dict[str, Any], takes: Sequence[dict[str, Any]], pool_n: int) -> dict[str, Any]:
    """Headline PnL is already on the ledger. Do not run the fail mix a second time."""
    chosen = [take for take in takes if take.get("id") == spec["id"] and isinstance(take.get("pnl"), int)]
    flat = [BookTrade(str(take["mint"]), int(take["t_ms"]), int(take["pnl"])) for take in chosen]
    stamped = [take for take in chosen if isinstance(take.get("p1"), int)]
    p1 = [BookTrade(str(take["mint"]), int(take["t_ms"]), int(take["p1"])) for take in stamped]
    p2 = [BookTrade(str(take["mint"]), int(take["t_ms"]), int(take["p2"])) for take in stamped if isinstance(take.get("p2"), int)]
    applied = bool(stamped)
    stats = combine_fail_models(
        book_stats(flat),
        book_stats(p1) if applied else None,
        book_stats(p2) if applied else None,
        applied=applied,
    )
    stats.update(
        {
            "id": spec["id"],
            "point": spec["point"],
            "fraction": spec["fraction"],
            "label": spec.get("label"),
            "pnl_rule": spec.get("pnl_rule"),
            "n_pool": pool_n,
        }
    )
    return stats


def _walk_frozen(
    rows: Sequence[DecisionRow],
    models: dict[tuple[str, str], Any],
    ledger: TakeLedger,
) -> tuple[list[dict[str, Any]], dict[str, RankWindow], dict[str, int]]:
    takes: list[dict[str, Any]] = []
    windows: dict[str, RankWindow] = {}
    pools: dict[str, int] = {}
    for spec in FROZEN_CANDIDATES:
        spec_id = str(spec["id"])
        point = str(spec["point"])
        fraction = float(spec["fraction"])
        rule = str(spec["pnl_rule"])
        label = str(spec["label"])
        model = None if label == "none" or fraction >= 1 else models.get((label, rule))
        window = ledger.window(spec_id, fraction if fraction > 0 else 1.0)
        windows[spec_id] = window
        pool = [
            row
            for row in rows
            if row.point_id() == point and isinstance(row.pnl_by_rule.get(rule), int)
        ]
        pools[spec_id] = len(pool)
        chosen = continue_take(pool, point, fraction, rule, model, FEATURE_NAMES, window)
        for row in chosen:
            takes.append(_compact(spec_id, rule, row))
    return takes, windows, pools


def _observe_paths(creates_dir: Path, days: set[str]) -> list[Path]:
    if not creates_dir.is_dir():
        return []
    found: list[Path] = []
    for path in creates_dir.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        name = path.name
        if not (name.startswith("observe-") or name.startswith("creates-")):
            continue
        if not (name.endswith(".jsonl") or name.endswith(".jsonl.zst") or name.endswith(".jsonl.gz")):
            continue
        if days and not any(day in name for day in days):
            continue
        found.append(path)
    return sorted(found)


def _live_rows(
    paths: Sequence[Path],
    creates_dir: Path,
    *,
    graph: Any,
    hop_ms: int,
    lags: Sequence[int],
    size_lamports: int,
    slippage_cap: float,
    curves: dict[str, Any],
) -> tuple[list[DecisionRow], list[int]]:
    starts = [s for s in (_hour_start_ms(path.name) for path in paths) if s is not None]
    if not starts:
        return [], []
    t_min = min(starts)
    t_max = max(starts) + 3_600_000
    days = {_hour_token(path.name)[:10] for path in paths if _hour_token(path.name)}
    creates = load_creates(_observe_paths(creates_dir, days), t_min_ms=t_min, t_max_ms=t_max)
    print(f"chunk_creates={len(creates)} files={len(paths)} rss_mib={_rss_mib()}", file=sys.stderr)
    books, stats = load_books(creates, paths)
    if stats.t_max_ms is None:
        _release_books(books)
        return [], list(getattr(stats, "chain_lags_ms", []) or [])
    kept = filter_window({mint: book.path for mint, book in books.items()}, stats.t_min_ms or t_min, stats.t_max_ms)
    books = {mint: books[mint] for mint in kept}
    use_lags = list(getattr(stats, "chain_lags_ms", []) or []) or list(lags)
    rows, _ticks, _wallet = build_dataset(
        books,
        tape_end_ms=stats.t_max_ms,
        chain_lags_ms=use_lags,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        graph=graph,
        hop_ms=hop_ms,
        emit_ticks=False,
    )
    stamp_pressure_pnls(rows, curves)
    chunk_lags = list(getattr(stats, "chain_lags_ms", []) or [])
    _release_books(books)
    del books
    gc.collect()
    print(f"chunk_decisions={len(rows)} rss_mib={_rss_mib()}", file=sys.stderr)
    return rows, chunk_lags


def _model_key(label: str, rule: str) -> str:
    return f"{label}__{rule}"


def _fit_cache_dir(output_dir: Path) -> Path:
    return output_dir / FIT_DIR


def _save_fit(
    output_dir: Path,
    *,
    models: dict[tuple[str, str], Any],
    mig_model: Any,
    curves: dict[str, Any],
    mig_curve_1: Any,
    mig_curve_2: Any,
    lags: Sequence[int],
    hop_ms: int,
    train_n: int,
    mig_labeled: int,
) -> None:
    root = _fit_cache_dir(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str | None] = {}
    for (label, rule), model in models.items():
        key = _model_key(label, rule)
        if model is None:
            files[key] = None
            continue
        files[key] = model.save(root / key)
    mig_name = None if mig_model is None else mig_model.save(root / "mig15_frozen")
    with (root / "curves.pkl").open("wb") as fh:
        pickle.dump(
            {"curves": curves, "mig_curve_1": mig_curve_1, "mig_curve_2": mig_curve_2, "lags": list(lags)},
            fh,
        )
    _atomic_json(
        root / "meta.json",
        {
            "schema": "laya_frozen_fit_v1",
            "freeze_at": CANDIDATE_FREEZE_AT,
            "train_n": train_n,
            "mig_labeled": mig_labeled,
            "hop_ms": hop_ms,
            "models": files,
            "mig15": mig_name,
        },
    )


def _load_fit(output_dir: Path) -> dict[str, Any] | None:
    root = _fit_cache_dir(output_dir)
    meta = _read_json(root / "meta.json")
    if meta.get("schema") != "laya_frozen_fit_v1" or meta.get("freeze_at") != CANDIDATE_FREEZE_AT:
        return None
    blob_path = root / "curves.pkl"
    if not blob_path.is_file():
        return None
    try:
        with blob_path.open("rb") as fh:
            blob = pickle.load(fh)
    except (OSError, pickle.UnpicklingError):
        return None
    models: dict[tuple[str, str], Any] = {}
    listed = meta.get("models") if isinstance(meta.get("models"), dict) else {}
    for spec in FROZEN_CANDIDATES:
        if str(spec["label"]) == "none" or float(spec["fraction"]) >= 1:
            continue
        key = _model_key(str(spec["label"]), str(spec["pnl_rule"]))
        name = listed.get(key)
        if not isinstance(name, str):
            return None
        path = root / name
        if not path.is_file():
            return None
        models[(str(spec["label"]), str(spec["pnl_rule"]))] = Booster.load(path)
    mig_model = None
    mig_name = meta.get("mig15")
    if isinstance(mig_name, str):
        if not (root / mig_name).is_file():
            return None
        mig_model = Booster.load(root / mig_name)
    print(f"frozen_fit cache hit train_n={meta.get('train_n')} rss_mib={_rss_mib()}", file=sys.stderr)
    return {
        "models": models,
        "mig_model": mig_model,
        "curves": blob.get("curves") or {},
        "mig_curve_1": blob.get("mig_curve_1"),
        "mig_curve_2": blob.get("mig_curve_2"),
        "lags": list(blob.get("lags") or []),
        "hop_ms": int(meta.get("hop_ms") or 0),
        "train_n": int(meta.get("train_n") or 0),
        "mig_labeled": int(meta.get("mig_labeled") or 0),
    }


def _fit_from_tape(
    *,
    tape_dir: Path,
    creates_dir: Path,
    output_dir: Path,
    graph: Any,
    attention: Sequence[Any],
    hop_ms: int,
    backend: str | None,
    size_lamports: int,
    slippage_cap: float,
) -> tuple[dict[str, Any], list[DecisionRow]]:
    files = prefer_sealed(live_train_tape(tape_dir, CANDIDATE_FREEZE_MS))
    print(f"frozen_train_files={len(files)} rss_mib={_rss_mib()}", file=sys.stderr)
    if not files:
        raise SystemExit("no pre-freeze live tape for the frozen fit")
    starts = [s for s in (_hour_start_ms(path.name) for path in files) if s is not None]
    t_min = min(starts) if starts else None
    t_max = (max(starts) + 3_600_000) if starts else CANDIDATE_FREEZE_MS + TRAIN_TAPE_PAD_MS
    days = {token[:10] for path in files if (token := _hour_token(path.name))}
    creates = load_creates(_observe_paths(creates_dir, days), t_min_ms=t_min, t_max_ms=t_max)
    books, stats = load_books(creates, files)
    if stats.t_min_ms is None or stats.t_max_ms is None:
        raise SystemExit("pre-freeze tape has no t_recv_ms")
    kept = filter_window({mint: book.path for mint, book in books.items()}, stats.t_min_ms, stats.t_max_ms)
    books = {mint: books[mint] for mint in kept}
    lags = list(getattr(stats, "chain_lags_ms", []) or [])
    print(f"frozen_train_creates={len(books)} lags={len(lags)} rss_mib={_rss_mib()}", file=sys.stderr)
    rows, _ticks, _wallet = build_dataset(
        books,
        tape_end_ms=stats.t_max_ms,
        chain_lags_ms=lags,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        graph=graph,
        hop_ms=hop_ms,
        emit_ticks=False,
    )
    train_rows = [row for row in rows if row.decision_t_ms <= CANDIDATE_FREEZE_MS]
    post_rows = [row for row in rows if row.decision_t_ms > CANDIDATE_FREEZE_MS]
    curves = fit_headline_curves(
        books,
        tape_end_ms=stats.t_max_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        create_t_max_ms=CANDIDATE_FREEZE_MS,
    )
    stamp_pressure_pnls(train_rows, curves)
    stamp_pressure_pnls(post_rows, curves)
    models = _fit_frozen(train_rows, backend)
    mig_model, mig_curve_1, mig_curve_2, mig_labeled = fit_mig15(
        books,
        tape_end_ms=stats.t_max_ms,
        graph=graph,
        attention=attention,
        freeze_ms=CANDIDATE_FREEZE_MS,
        backend=backend,
    )
    _release_books(books)
    del books
    gc.collect()
    packed = {
        "models": models,
        "mig_model": mig_model,
        "curves": curves,
        "mig_curve_1": mig_curve_1,
        "mig_curve_2": mig_curve_2,
        "lags": lags,
        "hop_ms": hop_ms,
        "train_n": len(train_rows),
        "mig_labeled": mig_labeled,
    }
    _save_fit(output_dir, **packed)
    print(
        f"frozen_fit wrote train_n={len(train_rows)} post_in_pad={len(post_rows)} rss_mib={_rss_mib()}",
        file=sys.stderr,
    )
    return packed, post_rows


def _overlap_paths(ordered: Sequence[Path], chunk: Sequence[Path]) -> list[Path]:
    if not chunk:
        return []
    names = [path.name for path in ordered]
    first = names.index(chunk[0].name)
    if first == 0:
        return list(chunk)
    return [ordered[first - 1], *chunk]


def _score_forward_chunks(
    *,
    tape_dir: Path,
    creates_dir: Path,
    ledger: TakeLedger,
    models: dict[tuple[str, str], Any],
    curves: dict[str, Any],
    lags: Sequence[int],
    hop_ms: int,
    size_lamports: int,
    slippage_cap: float,
    graph: Any,
    after_ms: int,
    pad_rows: Sequence[DecisionRow],
) -> None:
    if pad_rows:
        hours = sorted({decision_hour_key(row.decision_t_ms) for row in pad_rows})
        fresh = [hour for hour in hours if not ledger.has(hour)]
        if fresh:
            allow = set(fresh)
            rows = [row for row in pad_rows if decision_hour_key(row.decision_t_ms) in allow]
            takes, windows, pools = _walk_frozen(rows, models, ledger)
            ledger.add_chunk(fresh, takes, windows, pools, len(rows))
            print(f"forward_pad_hours={len(fresh)} takes={len(takes)} rss_mib={_rss_mib()}", file=sys.stderr)
        pad_rows.clear()
        gc.collect()
    # after_ms is the freeze. Hours already walked from the fit pad are in the ledger.
    sealed = sealed_live_hours(tape_dir, after_ms=after_ms)
    pending = [path for path in sealed if not ledger.has(_hour_token(path.name) or "")]
    for group in chunk_by_bytes(pending, chunk_bytes()):
        yield_for_runner()
        paths = _overlap_paths(sealed, group)
        allow = {_hour_token(path.name) for path in group}
        allow.discard(None)
        rows, _chunk_lags = _live_rows(
            paths,
            creates_dir,
            graph=graph,
            hop_ms=hop_ms,
            lags=lags,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            curves=curves,
        )
        kept = [
            row
            for row in rows
            if row.decision_t_ms > CANDIDATE_FREEZE_MS and decision_hour_key(row.decision_t_ms) in allow
        ]
        takes, windows, pools = _walk_frozen(kept, models, ledger)
        ledger.add_chunk(sorted(allow), takes, windows, pools, len(kept))
        print(
            f"forward_chunk hours={sorted(allow)} decisions={len(kept)} takes={len(takes)} rss_mib={_rss_mib()}",
            file=sys.stderr,
        )
        del rows, kept
        gc.collect()


def _backfill_rows(
    hours: Sequence[dict[str, Any]],
    *,
    lags: Sequence[int],
    hop_ms: int,
    graph: Any,
    curves: dict[str, Any],
    size_lamports: int,
    slippage_cap: float,
    seed_key: str,
) -> tuple[Any, list[DecisionRow], int]:
    if not lags:
        raise SystemExit("live tape has no chain→receive lags; refusing to stamp backfill")
    seed = LATENCY_DRAW_SEED ^ zlib.crc32(seed_key.encode("utf-8"))
    trade_draw = LagDraw(lags, hop_ms, seed)
    create_draw = LagDraw(lags, hop_ms, seed ^ CREATE_DRAW_SEED)
    creates = load_backfill_creates([hour.get("create") for hour in hours], create_draw)
    books, stats = load_books(creates, [hour["trade"] for hour in hours], prepare_row=trade_draw)
    tape_end = stats.t_max_ms or 0
    if tape_end <= 0:
        _release_books(books)
        return books, [], tape_end
    rows, _ticks, _wallet = build_dataset(
        books,
        tape_end_ms=tape_end,
        chain_lags_ms=list(lags),
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        graph=graph,
        hop_ms=0,
        emit_ticks=False,
    )
    stamp_pressure_pnls(rows, curves)
    return books, rows, tape_end


def _score_backward_chunks(
    *,
    backfill_dir: Path | None,
    ledger: TakeLedger,
    models: dict[tuple[str, str], Any],
    mig_model: Any,
    curves: dict[str, Any],
    mig_curve_1: Any,
    mig_curve_2: Any,
    lags: Sequence[int],
    hop_ms: int,
    graph: Any,
    attention: Sequence[Any],
    size_lamports: int,
    slippage_cap: float,
) -> None:
    if backfill_dir is None or not backfill_dir.is_dir():
        print("backward_holdout skipped, no backfill dir", file=sys.stderr)
        return
    hours = sealed_holdout_hours(backfill_dir)
    pending = [hour for hour in hours if not ledger.has(str(hour["hour"]))]
    print(f"backward_pending={len(pending)} sealed={len(hours)} rss_mib={_rss_mib()}", file=sys.stderr)
    by_hour = {str(hour["hour"]): hour for hour in hours}
    ordered_keys = [str(hour["hour"]) for hour in hours]
    groups = chunk_by_bytes([hour["trade"] for hour in pending], chunk_bytes())
    trade_to_hour = {hour["trade"]: hour for hour in pending}
    for group in groups:
        yield_for_runner()
        primary = [trade_to_hour[path] for path in group if path in trade_to_hour]
        if not primary:
            continue
        first = ordered_keys.index(str(primary[0]["hour"]))
        load_hours = list(primary)
        if first > 0:
            prev = by_hour.get(ordered_keys[first - 1])
            if prev is not None and prev not in load_hours:
                load_hours = [prev, *load_hours]
        allow = {str(hour["hour"]) for hour in primary}
        books, rows, tape_end = _backfill_rows(
            load_hours,
            lags=lags,
            hop_ms=hop_ms,
            graph=graph,
            curves=curves,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            seed_key=sorted(allow)[0],
        )
        mig_window = ledger.window(MIG15_ID, MIG15_FRACTION)
        mig_rows = _score_mig15(
            books,
            tape_end,
            graph,
            attention,
            mig_model,
            window=mig_window,
            hour_allow=allow,
        )
        _release_books(books)
        del books
        gc.collect()
        kept = [
            row
            for row in rows
            if decision_hour_key(row.decision_t_ms) in allow
        ]
        takes, windows, pools = _walk_frozen(kept, models, ledger)
        windows[MIG15_ID] = mig_window
        pools[MIG15_ID] = pools.get(MIG15_ID, 0) + len(mig_rows)
        takes.extend(_mig_records(mig_rows, mig_curve_1, mig_curve_2))
        ledger.add_chunk(sorted(allow), takes, windows, pools, len(kept) + len(mig_rows))
        print(
            f"backward_chunk hours={len(allow)} decisions={len(kept)} mig={len(mig_rows)} rss_mib={_rss_mib()}",
            file=sys.stderr,
        )
        del rows, kept
        gc.collect()


def _frozen_candidates_from_ledger(ledger: TakeLedger, train_n: int) -> dict[str, Any]:
    candidates = [
        _candidate_stats(dict(spec), ledger.takes, ledger.pool_n.get(str(spec["id"]), 0))
        for spec in FROZEN_CANDIDATES
    ]
    return {
        "freeze_at": CANDIDATE_FREEZE_AT,
        "freeze_ms": CANDIDATE_FREEZE_MS,
        "train_n": train_n,
        "holdout_n": ledger.rows_scored,
        "hours": list(ledger.hours),
        "separate_from_exploratory_search": True,
        "window": (
            "Sealed hours after the freeze, appended one chunk at a time. "
            f"Each chunk is at most {chunk_bytes()} compressed bytes plus the prior hour of prints. "
            "The rank window continues across chunks. Exploratory walk-forward is not in this table."
        ),
        "note": (
            "Fit only on decisions at or before the freeze. "
            "Scored only on decisions strictly after it. Not the exploratory search."
        ),
        "candidates": candidates,
    }


def _backward_report_from_ledger(
    ledger: TakeLedger,
    backfill_dir: Path | None,
    *,
    train_n: int,
    mig_labeled: int,
    hop_ms: int,
    lag_n: int,
) -> dict[str, Any]:
    hours = sealed_holdout_hours(backfill_dir) if backfill_dir is not None and backfill_dir.is_dir() else []
    scored = [hour for hour in hours if ledger.has(str(hour["hour"]))]
    days = day_status(scored)
    specs = [dict(spec) for spec in FROZEN_CANDIDATES]
    specs.append({"id": MIG15_ID, "point": "mig_15", "fraction": MIG15_FRACTION, "label": "pnl", "pnl_rule": DEPLOY_RULE_ID})
    day_rows = []
    for day in days:
        day_takes = [take for take in ledger.takes if decision_hour_key(int(take.get("t_ms") or 0))[:10] == day["day"]]
        candidates = [_candidate_stats(spec, day_takes, ledger.pool_n.get(str(spec["id"]), 0)) for spec in specs]
        day_rows.append({**day, "candidates": candidates})
    complete_days = {row["day"] for row in day_rows if row.get("complete")}
    sealed_takes = [
        take
        for take in ledger.takes
        if decision_hour_key(int(take.get("t_ms") or 0))[:10] in complete_days
    ]

    def _pool(takes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return [_candidate_stats(spec, takes, ledger.pool_n.get(str(spec["id"]), 0)) for spec in specs]

    return {
        "schema": "laya_backward_holdout_v1",
        "separate_from_forward_holdout": True,
        "holdout_end": HOLDOUT_END_ISO,
        "freeze_at": CANDIDATE_FREEZE_AT,
        "freeze_ms": CANDIDATE_FREEZE_MS,
        "train_n": train_n,
        "mig15_train_labeled": mig_labeled,
        "train_note": (
            "Boosters and fail curves are fit only on live decisions at or before "
            f"{CANDIDATE_FREEZE_AT}. Backfill rows are not in the fit. "
            "Hours are appended in chunks so the tape is not held all at once. "
            "A one-hour print overlap is context only and is not scored twice."
        ),
        "receive_clock": (
            "t_recv_ms = block_time*1000 + one live chain→receive draw per signature + the recv→decision hop. "
            "Each chunk draws from the pre-freeze live lag pool with a seed fixed by its first hour."
        ),
        "promotion": PROMOTION_RULE,
        "hop_ms": hop_ms,
        "lag_n": lag_n,
        "stamped_trades": 0,
        "dropped_clock": 0,
        "hours": [
            {"hour": hour["hour"], "block_time_start": hour["block_time_start"], "block_time_end": hour["block_time_end"]}
            for hour in scored
        ],
        "days": day_rows,
        "sealed_pool": _pool(sealed_takes),
        "scored_pool": _pool(ledger.takes),
        "scan": {"chunk_bytes": chunk_bytes(), "hours_scored": len(scored)},
    }


def _write_scoreboard(
    output_dir: Path,
    *,
    deploy: dict[str, Any],
    barrier: dict[str, Any],
    forward: dict[str, Any],
    backward: dict[str, Any],
    train_n: int,
) -> None:
    _preserve_once(output_dir / "scoreboard.json")
    board = {
        "schema": "laya_frozen_nightly_v1",
        "exploratory_search": "skipped",
        "exploratory_until": REVIEW_ON,
        "freeze_at": CANDIDATE_FREEZE_AT,
        "train_n": train_n,
        "entry": {
            "deploy": deploy,
            "barrier_deploy": barrier,
            "frozen_candidates": forward,
            "oos_n": 0,
        },
        "backward_holdout": backward,
        "assumptions": {
            "exploratory": (
                f"Walk-forward search and the deploy refit are off until {REVIEW_ON}. "
                "entry_model.txt, barrier_hit_100_30.txt, and mig15_model.txt were not rewritten."
            ),
            "frozen_holdout": forward.get("note"),
            "backward_holdout": backward.get("train_note"),
            "promotion": PROMOTION_RULE,
        },
    }
    _atomic_json(output_dir / "scoreboard.json", board)
    lines = [
        "# LAYA frozen nightly scoreboard",
        "",
        f"Exploratory retrain is skipped until {REVIEW_ON}.",
        "The forward books keep the deploy files already on disk.",
        "Frozen boosters are fit only on decisions at or before "
        f"{CANDIDATE_FREEZE_AT} ({train_n} decisions).",
        "",
        "## Pre-registered forward holdout",
        "",
        str(forward.get("window") or ""),
        "",
        "| candidate | point | take | n | pool | mean | mean 90% CI | total | ex top 3 | days+ | promote | p1 | p2 total |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for cand in forward.get("candidates") or []:
        lines.append(_md_frozen(cand))
    lines.append("")
    from tools.laya_backfill_holdout import format_backward_markdown

    lines.extend(format_backward_markdown(backward))
    text = "\n".join(lines)
    md = output_dir / "scoreboard.md"
    _preserve_once(md)
    md.write_text(text, encoding="utf-8")


def run_frozen_nightly(
    *,
    tape_dir: Path,
    creates_dir: Path,
    output_dir: Path,
    graph_dir: Path | None,
    attention_dir: Path | None,
    latency_report: Path | None,
    backfill_dir: Path | None,
    backend: str | None,
    size_lamports: int,
    slippage_cap: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    guarded = _guarded_paths(output_dir)
    before = _file_mtimes(guarded)
    deploy = _deploy_block(output_dir)
    barrier = _barrier_block(output_dir)
    which = available_backend(backend)
    hop_ms = recv_to_decision_hop_ms(load_latency_report(latency_report))
    graph = FundingGraph.load(graph_dir) if graph_dir is not None and graph_dir.is_dir() else None
    attention = load_attention(attention_dir) if attention_dir is not None and attention_dir.is_dir() else []
    print(f"frozen_nightly mode=frozen review={REVIEW_ON} backend={which} hop_ms={hop_ms}", file=sys.stderr)
    cached = _load_fit(output_dir)
    pad_rows: list[DecisionRow] = []
    if cached is None:
        cached, pad_rows = _fit_from_tape(
            tape_dir=tape_dir,
            creates_dir=creates_dir,
            output_dir=output_dir,
            graph=graph,
            attention=attention,
            hop_ms=hop_ms,
            backend=which,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
        )
    forward = TakeLedger(output_dir / FORWARD_STATE, output_dir / FORWARD_TAKES)
    backward = TakeLedger(output_dir / BACKWARD_STATE, output_dir / BACKWARD_TAKES)
    _score_forward_chunks(
        tape_dir=tape_dir,
        creates_dir=creates_dir,
        ledger=forward,
        models=cached["models"],
        curves=cached["curves"],
        lags=cached["lags"],
        hop_ms=int(cached["hop_ms"]),
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        graph=graph,
        after_ms=CANDIDATE_FREEZE_MS,
        pad_rows=pad_rows,
    )
    del pad_rows
    gc.collect()
    _score_backward_chunks(
        backfill_dir=backfill_dir,
        ledger=backward,
        models=cached["models"],
        mig_model=cached["mig_model"],
        curves=cached["curves"],
        mig_curve_1=cached["mig_curve_1"],
        mig_curve_2=cached["mig_curve_2"],
        lags=cached["lags"],
        hop_ms=int(cached["hop_ms"]),
        graph=graph,
        attention=attention,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
    )
    forward_board = _frozen_candidates_from_ledger(forward, int(cached["train_n"]))
    backward_board = _backward_report_from_ledger(
        backward,
        backfill_dir,
        train_n=int(cached["train_n"]),
        mig_labeled=int(cached["mig_labeled"]),
        hop_ms=int(cached["hop_ms"]),
        lag_n=len(cached["lags"]),
    )
    if backfill_dir is not None:
        _preserve_once(output_dir / "backward_holdout.json")
        _preserve_once(output_dir / "backward_holdout.md")
        write_backward_report(backward_board, output_dir)
    _write_scoreboard(
        output_dir,
        deploy=deploy,
        barrier=barrier,
        forward=forward_board,
        backward=backward_board,
        train_n=int(cached["train_n"]),
    )
    after = _file_mtimes(guarded)
    moved = [name for name, stamp in after.items() if before.get(name) != stamp]
    if moved:
        raise SystemExit("frozen nightly rewrote a deploy model: " + ", ".join(moved))
    print(
        f"frozen_nightly train_n={cached['train_n']} forward_hours={len(forward.hours)} "
        f"backward_hours={len(backward.hours)} rss_mib={_rss_mib()}",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Frozen LAYA nightly scoreboard. Does not retrain deploy models.")
    parser.add_argument("--tape-dir", type=Path, required=True)
    parser.add_argument("--creates-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--graph-dir", type=Path)
    parser.add_argument("--attention-dir", type=Path)
    parser.add_argument("--latency-report", type=Path)
    parser.add_argument("--backfill-dir", type=Path)
    parser.add_argument("--backend", choices=("lightgbm", "sklearn"))
    parser.add_argument("--size-sol", type=float, default=0.05)
    parser.add_argument("--slippage-cap", type=float, default=DEFAULT_SLIPPAGE_CAP)
    args = parser.parse_args(argv)
    if args.size_sol <= 0:
        raise SystemExit("size-sol must be positive")
    if nightly_mode() != "frozen":
        raise SystemExit(f"frozen nightly is only until {REVIEW_ON}")
    run_frozen_nightly(
        tape_dir=args.tape_dir,
        creates_dir=args.creates_dir,
        output_dir=args.output_dir,
        graph_dir=args.graph_dir,
        attention_dir=args.attention_dir,
        latency_report=args.latency_report,
        backfill_dir=args.backfill_dir,
        backend=args.backend,
        size_lamports=int(round(args.size_sol * 1_000_000_000)),
        slippage_cap=args.slippage_cap,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
