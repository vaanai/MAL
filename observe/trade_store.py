"""Hourly sealed tape files, zstd, retention, and a small completeness monitor.

Deletes only files in the tape directory whose names match trades-* or
pool-mints-* (.jsonl or .jsonl.zst). Stats files, observe JSONL, and anything
outside that directory are left alone. Disk pressure holds new writes in
process; it does not exit (systemd Restart=always would just refill the disk).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from observe.trade_decode import VENUE_BONDING, VENUE_PUMPSWAP, WSOL_MINT

log = logging.getLogger("mal.trade_tape")

HEADROOM_RATIO = 0.20
RESUME_RATIO = 0.25
# A rolled hour counts as full once the writer held it this long.
FULL_HOUR_S = 3000
ZSTD_LEVEL = 3

TAPE_FILE_RE = re.compile(
    r"^(trades|pool-mints)-(\d{4}-\d{2}-\d{2})(T(\d{2}))?\.jsonl(\.zst)?$"
)
LEGACY_DAILY_RE = re.compile(r"^(trades|pool-mints)-\d{4}-\d{2}-\d{2}\.jsonl$")
HOURLY_RAW_RE = re.compile(r"^(trades|pool-mints)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl$")
STATS_RE = re.compile(r"^stats-\d{4}-\d{2}-\d{2}\.jsonl$")

Clock = Callable[[], datetime]
FsBytes = Callable[[Path], tuple[int, int, int]]


def hour_stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def stored_trade(record: Mapping[str, Any]) -> dict[str, Any]:
    """PumpSwap rows drop fields a price path, wallet PnL, or graph edge can recompute.

    Bonding rows stay v1 (mint is on the event; the row is the smaller stream).
    """
    if record.get("venue") != VENUE_PUMPSWAP:
        return dict(record)
    slim: dict[str, Any] = {
        "v": 2,
        "venue": record.get("venue"),
        "mint": record.get("mint"),
        "trader": record.get("trader"),
        "side": record.get("side"),
        "sol_lamports": record.get("sol_lamports"),
        "token_raw": record.get("token_raw"),
        "quote_reserve": record.get("quote_reserve"),
        "base_reserve": record.get("base_reserve"),
        "price_sol": record.get("price_sol"),
        "pool": record.get("pool"),
        "slot": record.get("slot"),
        "signature": record.get("signature"),
        "event_index": record.get("event_index"),
        "t_recv_ms": record.get("t_recv_ms"),
        "event_ts": record.get("event_ts"),
    }
    if record.get("zero_sol"):
        slim["zero_sol"] = True
    quote_mint = record.get("quote_mint")
    if isinstance(quote_mint, str) and quote_mint and quote_mint != WSOL_MINT:
        slim["quote_mint"] = quote_mint
    return slim


def filesystem_bytes(path: Path) -> tuple[int, int, int]:
    """Return total, used, available-to-us bytes for the filesystem holding path."""
    st = os.statvfs(path)
    total = int(st.f_blocks) * int(st.f_frsize)
    avail = int(st.f_bavail) * int(st.f_frsize)
    used = total - int(st.f_bfree) * int(st.f_frsize)
    return total, used, avail


def file_stamp(path: Path) -> datetime | None:
    match = TAPE_FILE_RE.fullmatch(path.name)
    if match is None:
        return None
    day = match.group(2)
    hour = match.group(4)
    if hour is None:
        return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return datetime.strptime(f"{day}T{hour}", "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def hour_key(path: Path) -> str | None:
    match = TAPE_FILE_RE.fullmatch(path.name)
    if match is None or match.group(4) is None:
        return None
    return f"{match.group(2)}T{match.group(4)}"


def iter_tape_files(output_dir: Path) -> list[Path]:
    if not output_dir.is_dir():
        return []
    found: list[Path] = []
    for path in output_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        if TAPE_FILE_RE.fullmatch(path.name) is None:
            continue
        if path.parent.resolve() != output_dir.resolve():
            continue
        found.append(path)
    return found


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


class ZstdCompressor:
    """Compress sealed files on a background thread. Never touches the open file."""

    def __init__(self, level: int = ZSTD_LEVEL) -> None:
        self.level = level
        self._q: queue.Queue[Path | None] = queue.Queue()
        self._inflight: set[Path] = set()
        self._lock = threading.Lock()
        self.compressed = 0
        self.errors = 0
        self.raw_bytes = 0
        self.zst_bytes = 0
        self._thread = threading.Thread(target=self._run, name="tape-zstd", daemon=True)
        self._thread.start()

    def enqueue(self, path: Path) -> None:
        resolved = path.resolve()
        with self._lock:
            self._inflight.add(resolved)
        self._q.put(path)

    def inflight(self) -> set[Path]:
        with self._lock:
            return set(self._inflight)

    def sweep_startup(self, output_dir: Path, current: str) -> None:
        """Compress legacy daily files and hourly files that are not this hour."""
        if not output_dir.is_dir():
            return
        reap_partials(output_dir)
        for path in sorted(output_dir.iterdir()):
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name
            if LEGACY_DAILY_RE.fullmatch(name):
                self.enqueue(path)
                continue
            match = HOURLY_RAW_RE.fullmatch(name)
            if match is not None and match.group(2) != current:
                self.enqueue(path)

    def close(self, timeout_s: float = 120) -> None:
        self._q.put(None)
        self._thread.join(timeout=timeout_s)

    def _run(self) -> None:
        while True:
            path = self._q.get()
            if path is None:
                return
            try:
                raw, zst = compress_sealed(path, self.level)
                if raw > 0 and zst > 0:
                    self.raw_bytes += raw
                    self.zst_bytes += zst
                    self.compressed += 1
            except Exception as exc:
                self.errors += 1
                log.warning("zstd_failed path=%s err=%s", path.name, exc)
            finally:
                with self._lock:
                    self._inflight.discard(path.resolve())


def reap_partials(output_dir: Path) -> None:
    """Drop zstd partials whose writer pid is gone. Never follows symlinks."""
    if not output_dir.is_dir():
        return
    for path in output_dir.glob("*.jsonl.zst.partial.*"):
        if path.is_symlink() or not path.is_file():
            continue
        pid_text = path.name.rsplit(".", 1)[-1]
        if not pid_text.isdigit():
            continue
        sealed_name = path.name[: -len(f".partial.{pid_text}")]
        if TAPE_FILE_RE.fullmatch(sealed_name) is None:
            continue
        if Path(f"/proc/{pid_text}").exists():
            continue
        try:
            path.unlink()
        except OSError:
            continue


def compress_sealed(path: Path, level: int = ZSTD_LEVEL) -> tuple[int, int]:
    """Write path.zst via a partial rename, then unlink the raw file.

    Holds an exclusive flock for the duration so a restarted recorder does not
    compress the same sealed file twice. Returns (raw_bytes, zst_bytes).
    """
    if path.is_symlink() or not path.is_file():
        return (0, 0)
    dest = Path(str(path) + ".zst")
    zstd = shutil.which("zstd")
    if zstd is None:
        raise RuntimeError("zstd not installed")
    try:
        fh = path.open("rb")
    except OSError:
        return (0, 0)
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return (0, 0)
    partial = Path(f"{path}.zst.partial.{os.getpid()}")
    try:
        raw_size = path.stat().st_size
        if raw_size <= 0:
            path.unlink()
            return (0, 0)
        if dest.exists() and not dest.is_symlink() and dest.stat().st_size > 0:
            path.unlink()
            return (raw_size, dest.stat().st_size)
        subprocess.run(
            [zstd, f"-{level}", "-q", "-f", "-o", str(partial), str(path)],
            check=True,
            timeout=3600,
        )
        os.replace(partial, dest)
        path.unlink()
        return (raw_size, dest.stat().st_size)
    finally:
        if partial.exists() and not partial.is_symlink():
            partial.unlink()
        fh.close()


class HourlyJsonlWriter:
    def __init__(
        self,
        output_dir: Path,
        prefix: str,
        compressor: ZstdCompressor,
        *,
        clock: Clock | None = None,
        on_seal: Callable[[str, float], None] | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.prefix = prefix
        self.compressor = compressor
        self.rows = 0
        self.bytes = 0
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._on_seal = on_seal
        self._fh = None
        self._hour: str | None = None
        self._opened_at: datetime | None = None
        self.path: Path | None = None

    def write(self, record: Mapping[str, Any]) -> None:
        hour = hour_stamp(self._clock())
        if hour != self._hour or self._fh is None:
            self._roll(hour)
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        assert self._fh is not None
        self._fh.write(line)
        self._fh.flush()
        self.rows += 1
        self.bytes += len(line.encode("utf-8"))

    def rotate(self) -> None:
        if self._fh is None:
            return
        hour = hour_stamp(self._clock())
        if hour != self._hour:
            self._roll(hour)

    def _roll(self, hour: str) -> None:
        previous = self.path
        opened = self._opened_at
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        if previous is not None and opened is not None:
            span = (self._clock() - opened).total_seconds()
            if previous.is_file() and previous.stat().st_size > 0:
                if self._on_seal is not None and self._hour is not None:
                    self._on_seal(self._hour, span)
                self.compressor.enqueue(previous)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / f"{self.prefix}-{hour}.jsonl"
        self._fh = self.path.open("a", encoding="utf-8")
        self._hour = hour
        self._opened_at = self._clock()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def _on_disk_hour_sizes(output_dir: Path, skip: set[Path]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for path in iter_tape_files(output_dir):
        if path.resolve() in skip:
            continue
        key = hour_key(path)
        if key is None:
            continue
        if path.name.endswith(".jsonl") and Path(str(path) + ".zst").exists():
            continue
        try:
            sizes[key] = sizes.get(key, 0) + path.stat().st_size
        except OSError:
            continue
    return sizes


def estimate_bytes_per_day(
    output_dir: Path,
    hour_spans: Mapping[str, float],
    skip: set[Path],
) -> float | None:
    sizes = _on_disk_hour_sizes(output_dir, skip)
    measured: list[float] = []
    unknown: list[int] = []
    for stamp, size in sizes.items():
        if size <= 0:
            continue
        span = hour_spans.get(stamp)
        if span is not None and span >= FULL_HOUR_S:
            measured.append(size / span * 86400)
        elif span is None:
            unknown.append(size)
    if measured:
        return sum(measured) / len(measured)
    if len(unknown) >= 2:
        unknown.sort()
        median = unknown[len(unknown) // 2]
        return float(median * 24)
    return None


def keep_days_for(total: int, used: int, tape_bytes: int, bytes_per_day: float) -> int:
    """Days of compressed tape that leave 20% of the volume free."""
    if bytes_per_day <= 0 or total <= 0:
        return 0
    non_tape = max(0, used - tape_bytes)
    usable = (1 - HEADROOM_RATIO) * total - non_tape
    if usable <= 0:
        return 0
    return int(usable // bytes_per_day)


class DiskGuard:
    def __init__(self, output_dir: Path, *, fs_bytes: FsBytes = filesystem_bytes) -> None:
        self.output_dir = output_dir
        self.holding = False
        self.dropped = 0
        self.keep_days: int | None = None
        self.bytes_per_day: float | None = None
        self.free_ratio = 1.0
        self.hour_spans: dict[str, float] = {}
        self._fs = fs_bytes

    def note_hour(self, stamp: str, span_s: float) -> None:
        self.hour_spans[stamp] = span_s

    def tick(
        self,
        *,
        now: datetime | None = None,
        open_paths: set[Path] | None = None,
        inflight: set[Path] | None = None,
        current_hour: str | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        open_resolved = {path.resolve() for path in (open_paths or set())}
        inflight_resolved = {path.resolve() for path in (inflight or set())}
        skip = open_resolved | inflight_resolved
        self.bytes_per_day = estimate_bytes_per_day(self.output_dir, self.hour_spans, skip)
        total, used, avail = self._fs(self.output_dir)
        tape = _tape_bytes(self.output_dir)
        if self.bytes_per_day is not None and total > 0:
            days = keep_days_for(total, used, tape, self.bytes_per_day)
            if days != self.keep_days:
                log.info(
                    "retention keep_days=%s bytes_per_day=%.0f",
                    days,
                    self.bytes_per_day,
                )
            self.keep_days = days
            cutoff = now - timedelta(days=days)
            self._delete_older(cutoff, open_resolved, inflight_resolved, current_hour)
        self._free_until_headroom(open_resolved, inflight_resolved, current_hour)
        total, _used, avail = self._fs(self.output_dir)
        ratio = (avail / total) if total else 1.0
        self.free_ratio = ratio
        if ratio < HEADROOM_RATIO:
            if not self.holding:
                log.error(
                    "disk_guard hold free_ratio=%.3f keep_days=%s dropped_next_writes=1",
                    ratio,
                    self.keep_days,
                )
            self.holding = True
        elif self.holding and ratio >= RESUME_RATIO:
            log.warning("disk_guard resume free_ratio=%.3f", ratio)
            self.holding = False


    def _delete_older(
        self,
        cutoff: datetime,
        open_paths: set[Path],
        inflight: set[Path],
        current_hour: str | None,
    ) -> None:
        for path in self._candidates(open_paths, inflight, current_hour):
            stamp = file_stamp(path)
            if stamp is None or stamp >= cutoff:
                continue
            self._unlink(path, "age")

    def _free_until_headroom(
        self,
        open_paths: set[Path],
        inflight: set[Path],
        current_hour: str | None,
    ) -> None:
        for _ in range(10_000):
            total, _used, avail = self._fs(self.output_dir)
            if total <= 0 or avail / total >= HEADROOM_RATIO:
                return
            victim = self._oldest(open_paths, inflight, current_hour)
            if victim is None:
                return
            self._unlink(victim, "headroom")

    def _candidates(
        self,
        open_paths: set[Path],
        inflight: set[Path],
        current_hour: str | None,
    ) -> list[Path]:
        found: list[Path] = []
        for path in iter_tape_files(self.output_dir):
            resolved = path.resolve()
            if resolved in open_paths or resolved in inflight:
                continue
            if current_hour is not None and hour_key(path) == current_hour:
                continue
            found.append(path)
        return found

    def _oldest(
        self,
        open_paths: set[Path],
        inflight: set[Path],
        current_hour: str | None,
    ) -> Path | None:
        ranked: list[tuple[datetime, str, Path]] = []
        for path in self._candidates(open_paths, inflight, current_hour):
            stamp = file_stamp(path)
            if stamp is None:
                continue
            ranked.append((stamp, path.name, path))
        if not ranked:
            return None
        ranked.sort()
        return ranked[0][2]

    def _unlink(self, path: Path, reason: str) -> None:
        if path.is_symlink() or TAPE_FILE_RE.fullmatch(path.name) is None:
            return
        if path.parent.resolve() != self.output_dir.resolve():
            return
        try:
            path.unlink()
        except OSError as exc:
            log.warning("retention_unlink_failed path=%s err=%s", path.name, exc)
            return
        log.warning("retention_delete path=%s reason=%s", path.name, reason)


def _tape_bytes(output_dir: Path) -> int:
    total = 0
    for path in iter_tape_files(output_dir):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


class CompletenessMonitor:
    """Trades/min, create coverage, reconnects, lag. Scores the previous window."""

    def __init__(
        self,
        output_dir: Path,
        observe_dir: Path,
        stats: Any,
        *,
        interval_s: float = 600,
        clock: Clock | None = None,
        start_at_end: bool = True,
    ) -> None:
        self.output_dir = output_dir
        self.observe_dir = observe_dir
        self.stats = stats
        self.interval_s = interval_s
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._window_start = 0.0
        self._use_monotonic = True
        self.creates_prev: set[str] = set()
        self.creates_cur: set[str] = set()
        self.bonding_mints: set[str] = set()
        self.trades = 0
        self.bonding = 0
        self.pumpswap = 0
        self.lags: list[float] = []
        self._reconnects_mark = int(getattr(stats, "reconnects", 0) or 0)
        self._offsets: dict[str, int] = {}
        self._partial: dict[str, bytes] = {}
        self._primed = False
        self._start_at_end = start_at_end
        self.rows_written = 0

    def start(self, monotonic_now: float) -> None:
        self._window_start = monotonic_now
        self._prime_observe()

    def note(self, record: Mapping[str, Any]) -> None:
        self.trades += 1
        venue = record.get("venue")
        if venue == VENUE_BONDING:
            self.bonding += 1
            mint = record.get("mint")
            if isinstance(mint, str) and mint:
                self.bonding_mints.add(mint)
            self._note_lag(record)
        elif venue == VENUE_PUMPSWAP:
            self.pumpswap += 1

    def _note_lag(self, record: Mapping[str, Any]) -> None:
        try:
            lag = float(record["t_recv_ms"]) / 1000.0 - float(record["event_ts"])
        except (KeyError, TypeError, ValueError):
            return
        if 0 <= lag < 120:
            self.lags.append(lag)

    def maybe_flush(self, monotonic_now: float, disk: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        self._read_creates()
        if monotonic_now - self._window_start < self.interval_s:
            return None
        row = self._emit(monotonic_now, disk or {})
        return row

    def flush(self, monotonic_now: float, disk: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._read_creates()
        return self._emit(monotonic_now, disk or {})

    def _emit(self, monotonic_now: float, disk: Mapping[str, Any]) -> dict[str, Any]:
        window_s = max(monotonic_now - self._window_start, 0.001)
        creates = len(self.creates_prev)
        matched = sum(1 for mint in self.creates_prev if mint in self.bonding_mints)
        pct = (100.0 * matched / creates) if creates else None
        reconnects = int(getattr(self.stats, "reconnects", 0) or 0)
        delta = max(0, reconnects - self._reconnects_mark)
        now = self._clock()
        row: dict[str, Any] = {
            "v": 1,
            "type": "tape_stats",
            "t": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_s": round(window_s, 3),
            "trades": self.trades,
            "trades_per_min": self.trades / (window_s / 60.0),
            "bonding": self.bonding,
            "pumpswap": self.pumpswap,
            "creates": creates,
            "creates_with_bonding_trade": matched,
            "pct_creates_with_bonding_trade": pct,
            "reconnects": delta,
            "lag_p50_s": _percentile(self.lags, 0.50),
            "lag_p99_s": _percentile(self.lags, 0.99),
            "disk_free_bytes": disk.get("free_bytes"),
            "disk_total_bytes": disk.get("total_bytes"),
            "hold": bool(disk.get("hold")),
        }
        self._append(row)
        self.creates_prev = set(self.creates_cur)
        self.creates_cur = set()
        self.trades = 0
        self.bonding = 0
        self.pumpswap = 0
        self.lags = []
        self._reconnects_mark = reconnects
        self._window_start = monotonic_now
        self.rows_written += 1
        return row

    def _append(self, row: dict[str, Any]) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            day = self._clock().astimezone(timezone.utc).strftime("%Y-%m-%d")
            path = self.output_dir / f"stats-{day}.jsonl"
            line = json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
        except OSError as exc:
            log.warning("stats_write_failed err=%s", exc)

    def _prime_observe(self) -> None:
        if self._primed:
            return
        self._primed = True
        if not self._start_at_end or not self.observe_dir.is_dir():
            return
        for path in self.observe_dir.glob("observe-*.jsonl"):
            try:
                self._offsets[path.name] = path.stat().st_size
            except OSError:
                continue

    def _read_creates(self) -> None:
        self._prime_observe()
        if not self.observe_dir.is_dir():
            return
        for path in sorted(self.observe_dir.glob("observe-*.jsonl")):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            offset = self._offsets.get(path.name, 0)
            if size < offset:
                offset = 0
                self._partial.pop(path.name, None)
            if size == offset and path.name not in self._partial:
                continue
            try:
                with path.open("rb") as fh:
                    fh.seek(offset)
                    data = fh.read()
                    self._offsets[path.name] = fh.tell()
            except OSError:
                continue
            blob = self._partial.pop(path.name, b"") + data
            if not blob.endswith(b"\n"):
                nl = blob.rfind(b"\n")
                if nl < 0:
                    self._partial[path.name] = blob
                    continue
                self._partial[path.name] = blob[nl + 1 :]
                blob = blob[: nl + 1]
            for line in blob.splitlines():
                self._take_create(line)

    def _take_create(self, line: bytes) -> None:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(obj, dict):
            return
        if obj.get("stream") != "subscribeNewToken" and obj.get("txType") != "create":
            return
        mint = obj.get("mint")
        if isinstance(mint, str) and mint and mint != "UNK":
            self.creates_cur.add(mint)
