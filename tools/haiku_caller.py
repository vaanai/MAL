"""haiku_caller: the one place the paper Haiku trader calls the Anthropic API, with a hard spend stop and a replay cache.

Run: python -m tools.haiku_caller --request REQ.json --out RESP.json --ledger LEDGER.jsonl --budget-usd 25 \
         [--save-dir DIR] [--replay | --replay-only] [--model ID --allow-model ID [--prices-json '{...}']]
Use: from tools.haiku_caller import HaikuCaller; HaikuCaller(ledger, budget_usd, save_dir=...).call(messages=[...], max_tokens=300)

Serves /data/mal/hunt-1008/haiku-trader-v2/DESIGN.md section 4 (decision call) and section 6 (caller: spend cap, replay mode).
PAPER research only. Nothing here trades, holds a wallet, or reads a wallet key.

The key. It is read ONLY from the environment variable ANTHROPIC_API_KEY, at the moment a call is about to be made. The job
that runs this module loads that variable from a file; this module never opens a key file and never looks in
/var/lib/mal/anthropic/. The key is held in one local variable and handed to the transport. It is never stored on an object,
never written to stdout, stderr, the ledger or the text store, and never put in an exception: every message in this module is
built from numbers, status codes and fixed words, and a server's error text is never copied (only the server's error.type, and
only when it looks like a short snake_case token). Transport exceptions are caught and replaced by a class label, with the
original chained away, so no request header can travel in a traceback. Redirects are refused, because urllib would otherwise
re-send the key header to wherever a redirect pointed.

The hard stop. The server cannot enforce a spend limit, so this module does. A JSONL ledger and a budget_usd are required. Under
an exclusive flock on <ledger>.lock, every HTTP attempt does: read the ledger, sum cost_usd over EVERY row (so the budget holds
across runs and processes), compute a worst case for this attempt, and refuse with BudgetExceeded, without calling and without
appending, if spent + worst case > budget. Otherwise it RESERVES, then calls, then SETTLES:
  1. append a "reserve" row whose cost_usd is the attempt's worst case (fsynced before the request can leave the machine; if it
     cannot be written, nothing is sent);
  2. send the request;
  3. append a "settle" row with the same reservation_id whose cost_usd is (what the attempt really cost - the reserved worst
     case), usually negative.
Spent is always the plain sum of cost_usd over all rows, so a reserve plus its settle adds up to the actual cost. When the actual
cost is not known the settle row's cost_usd is 0.0 and the worst case simply stays charged (cost_basis below): a timeout, a dropped
connection, any exception from the transport, a 200 whose body cannot be read. A process that dies between the reserve and the
settle (SIGKILL, power loss, a KeyboardInterrupt in the transport) leaves the reserve row alone, and the worst case stays charged;
that is intended, and ledger_summary reports it as reserved_unsettled. The lock is held across the HTTP call on purpose: two
processes cannot both pass the check and then both spend. The cost is that calls on one ledger are serial. Retry back-off sleeps
are taken with the lock released. Pre-request failures (bad arguments, a missing key, a budget refusal) write no rows.

Worst case = ceil(utf-8 bytes of the system+messages JSON / 3) input tokens at the input rate + max_tokens at the output rate.
One deliberate tightening of that formula: if the request carries a cache_control marker, the input part is priced at the
(higher) cache-write rate, so a cache write cannot make the real charge exceed the estimate. Actual cost comes from the response
usage fields (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens; a missing or invalid field is 0).
If the real usage ever exceeds the estimate (a prompt that tokenises worse than 3 bytes per token), the settle row is positive and
the ledger records the real cost; the check for the NEXT attempt then sees it.

What is still not covered: an HTTP error response (4xx/5xx with a body) is assumed not billed and settles to 0. The 1-hour cache
write price is not in the table. The workspace spend limit the owner sets in the Anthropic console remains the backstop; this
module is the first stop, not the only one.

Ledger rows. A ledger is append-only JSONL; no prompt or response text is ever in it. Every row has kind and cost_usd. A row with no
kind (not written by this module) is a plain cost row, cost_usd >= 0. A reader fails closed (LedgerError) on any row it cannot
trust: bad JSON, unknown kind, a settle with no earlier reserve or settling twice, a reserve+settle pair that nets below 0.
  reserve: kind, ts, reservation_id (fresh per attempt), model, request_sha256, attempt, cost_usd (the worst case). No usage.
  settle: kind, ts, reservation_id, model, request_sha256, response_sha256 (raw bytes, or null), http_status (null if no
  response), request_id (response header request-id, if it looks like an id), stop_reason, input_tokens, output_tokens,
  cache_creation_input_tokens, cache_read_input_tokens, cost_usd (actual - reserved; 0.0 for the worst-case bases), cost_basis,
  latency_ms, attempt, thinking (the parameter exactly as sent, null if omitted), allow_model, custom_prices, error_class,
  error_type.
  cost_basis: "usage" (200 with a readable body; actual cost from usage), "http_error" (an error response; actual 0),
  "worst_case_no_response" (timeout, dropped connection, transport exception) or "worst_case_unreadable_200" (a 200 whose body is
  not a JSON object; billed, usage unknown). The two worst_case bases leave the reserved worst case charged.
  error_class is one of null, "HTTPError", "Timeout", "NetworkError", "TransportError", "MalformedResponse"; error_type is the
  server's error.type or null.
ledger_summary(path) reports total_cost_usd (what counts against the budget), settled_actual_cost_usd,
worst_case_settled_count/_cost_usd and reserved_unsettled_count/_cost_usd separately; the first is exactly the sum of the other three.

Retries. HTTP 429, 500, 502, 503, 529, a timeout and a dropped connection are retried, at most 3 attempts in total, sleeping 2 s
then 8 s (or the retry-after header if it asks for longer, capped at 30 s). Any other non-200 status is not retried and raises
ApiError. A 200 whose body is not a JSON object is an ApiError too. The timeout is 60 s per attempt. A dropped connection
(refused, reset, DNS) is treated like a timeout: no response, so retry within the same 3-attempt, budget-checked loop.

Text store and replay cache. With save_dir, each successful call writes <request_sha256>.json (the request body without headers,
and the response JSON) with mode 600 in a directory created with mode 700. The same directory is the replay cache: with replay,
a request whose file exists returns the saved response with no call, no key needed and no ledger row. With replay_only, a miss
raises ReplayMiss instead of calling. The request hash is the sha256 of the canonical body: json.dumps(sort_keys=True,
separators=(",", ":")), UTF-8. The file on the wire is exactly those bytes.

Models. DEFAULT_MODEL is the only model with a built-in price. Any other model id is refused unless the caller passes
allow_model equal to it AND a price dict (per million tokens: input, cache_write, cache_read, output). A price dict for a
model that is in the table is refused: the table is the one source of truth. Thinking defaults to {"type": "disabled"}
(Haiku 5.5 thinks by default and thinking is billed as output); pass thinking=None to omit the parameter.

Exit codes (CLI): 0 ok, 2 bad arguments / missing key / unusable ledger, 3 budget, 4 replay miss, 5 API error, 6 retries exhausted.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import hashlib
import http.client
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_THINKING = {"type": "disabled"}

# USD per million tokens.
PRICES = {
    DEFAULT_MODEL: {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50},
}
PRICE_KEYS = ("input", "cache_write", "cache_read", "output")

TIMEOUT_S = 60
MAX_ATTEMPTS = 3
BACKOFF_S = (2, 8)
RETRY_AFTER_CAP_S = 30
RETRY_STATUSES = frozenset({429, 500, 502, 503, 529})
BYTES_PER_TOKEN_ESTIMATE = 3

EXIT_OK, EXIT_BAD_ARGS, EXIT_BUDGET, EXIT_REPLAY_MISS, EXIT_API_ERROR, EXIT_RETRIES = 0, 2, 3, 4, 5, 6

REQUEST_KEYS = frozenset({"system", "messages", "max_tokens", "temperature", "thinking"})
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

_KEY_OK = re.compile(r"^[\x21-\x7e]{1,512}$")
_TOKEN_OK = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_ERROR_TYPE_OK = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA_OK = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------- errors

class HaikuCallerError(Exception):
    """Base class. exit_code is what the CLI returns. Messages never contain the key or server text."""
    exit_code = 1


class BadArguments(HaikuCallerError):
    exit_code = EXIT_BAD_ARGS


class ModelRefused(BadArguments):
    pass


class MissingKey(BadArguments):
    pass


class LedgerError(HaikuCallerError):
    """The ledger or its lock cannot be read or written, or a row is not a valid cost row. Fails closed: no call is made."""
    exit_code = EXIT_BAD_ARGS


class BudgetExceeded(HaikuCallerError):
    exit_code = EXIT_BUDGET


class ReplayMiss(HaikuCallerError):
    exit_code = EXIT_REPLAY_MISS


class ReplayCorrupt(ReplayMiss):
    """A saved entry exists but is unreadable or does not match its own request hash."""


class ApiError(HaikuCallerError):
    exit_code = EXIT_API_ERROR

    def __init__(self, message: str, *, status: int | None = None, error_type: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_type = error_type


class RetriesExhausted(HaikuCallerError):
    exit_code = EXIT_RETRIES

    def __init__(self, message: str, *, attempts: int, last_status: int | None, last_error_class: str | None):
        super().__init__(message)
        self.attempts = attempts
        self.last_status = last_status
        self.last_error_class = last_error_class


# ---------------------------------------------------------------- pure helpers

def canonical_bytes(obj) -> bytes:
    """The canonical JSON bytes: sort_keys, separators (",", ":"), UTF-8. Raises BadArguments if obj is not plain JSON."""
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise BadArguments("request is not plain JSON (non-serialisable value, NaN or Infinity)") from None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_prices(prices) -> dict:
    if not isinstance(prices, dict) or set(prices) != set(PRICE_KEYS):
        raise ModelRefused("prices must be a dict with exactly the keys " + ", ".join(PRICE_KEYS) + " (USD per million tokens)")
    out = {}
    for k in PRICE_KEYS:
        if not _is_num(prices[k]) or prices[k] < 0:
            raise ModelRefused("every price must be a finite number >= 0")
        out[k] = float(prices[k])
    return out


def resolve_prices(model: str, allow_model, prices) -> tuple[dict, bool]:
    """(price table for this call, whether it is a caller-supplied table). Refuses a model it cannot price."""
    if not isinstance(model, str) or not model:
        raise ModelRefused("model must be a non-empty string")
    if model in PRICES:
        if prices is not None:
            raise ModelRefused("a price dict is only accepted for a model that is not in the built-in table")
        if model != DEFAULT_MODEL and allow_model != model:
            raise ModelRefused(f"model {model!r} is not the default model; pass allow_model equal to it")
        return dict(PRICES[model]), False
    if allow_model != model:
        raise ModelRefused(f"model {model!r} is not the default model {DEFAULT_MODEL!r}; pass allow_model equal to it")
    if prices is None:
        raise ModelRefused(f"model {model!r} has no built-in price; pass a price dict as well")
    return validate_prices(prices), True


def _prompt_bytes(system, messages) -> bytes:
    payload = {"messages": messages}
    if system is not None:
        payload["system"] = system
    return canonical_bytes(payload)


def estimate_input_tokens(system, messages) -> int:
    return math.ceil(len(_prompt_bytes(system, messages)) / BYTES_PER_TOKEN_ESTIMATE)


def worst_case_cost(system, messages, max_tokens: int, prices: dict) -> float:
    """USD upper bound for one attempt: estimated input tokens plus max_tokens of output."""
    raw = _prompt_bytes(system, messages)
    in_rate = prices["input"]
    if b'"cache_control"' in raw:
        in_rate = max(in_rate, prices["cache_write"])
    return (math.ceil(len(raw) / BYTES_PER_TOKEN_ESTIMATE) * in_rate + max_tokens * prices["output"]) / 1e6


def _tok(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def usage_counts(usage) -> dict:
    u = usage if isinstance(usage, dict) else {}
    return {k: _tok(u.get(k)) for k in USAGE_KEYS}


def cost_from_usage(counts: dict, prices: dict) -> float:
    return (counts["input_tokens"] * prices["input"]
            + counts["cache_creation_input_tokens"] * prices["cache_write"]
            + counts["cache_read_input_tokens"] * prices["cache_read"]
            + counts["output_tokens"] * prices["output"]) / 1e6


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_token(v, rx=_TOKEN_OK):
    return v if isinstance(v, str) and rx.match(v) else None


def _server_error_type(resp_json):
    err = resp_json.get("error") if isinstance(resp_json, dict) else None
    return _safe_token(err.get("type"), _ERROR_TYPE_OK) if isinstance(err, dict) else None


def _retry_delay(attempt: int, headers: dict) -> float:
    """Seconds to sleep after failed attempt number `attempt` (1-based): the back-off, or retry-after if longer, capped at 30."""
    backoff = float(BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1])
    try:
        ra = float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return backoff
    if not math.isfinite(ra) or ra < 0:
        return backoff
    return max(backoff, min(ra, float(RETRY_AFTER_CAP_S)))


# ---------------------------------------------------------------- file helpers

def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _write_private(path, data: bytes) -> None:
    """Write data to path with mode 600, also when the file already existed with wider modes (chmod before any byte lands)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, data)
    finally:
        os.close(fd)


def _write_private_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.close(fd)
    os.replace(tmp, path)


# ---------------------------------------------------------------- ledger

class Ledger:
    """Append-only JSONL of attempts plus a flock'd <path>.lock. Holds no key and no text."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextlib.contextmanager
    def locked(self, exclusive: bool = True):
        """flock on the lock file. A shared (read) lock falls back to no lock if the lock file cannot be opened (read-only dir)."""
        fd = None
        try:
            if exclusive:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        except OSError:
            if fd is not None:
                os.close(fd)
            if exclusive:
                raise LedgerError("cannot open or lock the ledger lock file") from None
            fd = None
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)  # closing the descriptor releases the flock

    def rows(self) -> list[dict]:
        """Every row, validated; anything unexpected raises LedgerError (fail closed, no call is made).

        A row is a JSON object with a finite cost_usd and a kind: absent (a plain cost row, cost_usd >= 0), "reserve"
        (cost_usd >= 0, a unique reservation_id) or "settle" (cost_usd may be negative: it is actual cost minus the reserved
        worst case; it must name an earlier reserve row, only once, and the pair must not net below 0, so no row can
        lower the spend below what was really committed). Any other kind is refused."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError:
            raise LedgerError("cannot read the ledger") from None
        out = []
        reserved: dict[str, float] = {}
        settled: set[str] = set()
        for n, line in enumerate(raw.split(b"\n"), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise LedgerError(f"ledger line {n} is not valid JSON; refusing to continue") from None
            if not isinstance(row, dict) or not _is_num(row.get("cost_usd")):
                raise LedgerError(f"ledger line {n} has no valid cost_usd; refusing to continue")
            kind, cost, rid = row.get("kind"), row["cost_usd"], row.get("reservation_id")
            if kind is None:
                ok = cost >= 0
            elif kind == "reserve":
                ok = cost >= 0 and isinstance(rid, str) and rid != "" and rid not in reserved
                if ok:
                    reserved[rid] = cost
            elif kind == "settle":
                ok = isinstance(rid, str) and rid in reserved and rid not in settled and reserved[rid] + cost >= 0
                if ok:
                    settled.add(rid)
            else:
                ok = False
            if not ok:
                raise LedgerError(f"ledger line {n} is not a valid {kind or 'cost'} row; refusing to continue")
            out.append(row)
        return out

    def spent(self) -> float:
        return math.fsum(r["cost_usd"] for r in self.rows())

    def open_append(self) -> int:
        try:
            return os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError:
            raise LedgerError("cannot open the ledger for append") from None

    @staticmethod
    def append_row(fd: int, row: dict) -> None:
        line = json.dumps(row, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        try:
            _write_all(fd, line)
            os.fsync(fd)
        except OSError:
            raise LedgerError("cannot append to the ledger") from None


def ledger_summary(path) -> dict:
    """Totals over a ledger, for probes and the dashboard line. Raises LedgerError on a malformed row.

    total_cost_usd is what counts against the budget (the sum of cost_usd over every row). It splits exactly into
    settled_actual_cost_usd (attempts settled from a response: usage, or an HTTP error priced 0; plus plain cost rows),
    worst_case_settled_cost_usd (attempts with no usable response, charged at their reserved worst case) and
    reserved_unsettled_cost_usd (reservations with no settle row: a process died or was killed mid-call; still charged at
    worst case). calls counts attempts started; the error and token counts and the latency median read the attempts that
    have a settle row (or are plain rows)."""
    ledger = Ledger(path)
    with ledger.locked(exclusive=False):
        rows = ledger.rows()
    reserves = {r["reservation_id"]: r for r in rows if r.get("kind") == "reserve"}
    done = [r for r in rows if r.get("kind") != "reserve"]  # settle rows and plain rows: the finished attempts
    settled_ids = {r["reservation_id"] for r in rows if r.get("kind") == "settle"}
    tokens = {"input": 0, "output": 0, "cache_creation_input": 0, "cache_read_input": 0}
    actual, worst_settled, worst_n = [], [], 0
    for r in done:
        tokens["input"] += _tok(r.get("input_tokens"))
        tokens["output"] += _tok(r.get("output_tokens"))
        tokens["cache_creation_input"] += _tok(r.get("cache_creation_input_tokens"))
        tokens["cache_read_input"] += _tok(r.get("cache_read_input_tokens"))
        if r.get("kind") == "settle":
            charged = reserves[r["reservation_id"]]["cost_usd"] + r["cost_usd"]
            if str(r.get("cost_basis", "")).startswith("worst_case"):
                worst_settled.append(charged)
                worst_n += 1
            else:
                actual.append(charged)
        else:
            actual.append(r["cost_usd"])
    unsettled = [r["cost_usd"] for rid, r in reserves.items() if rid not in settled_ids]
    lat200 = [r["latency_ms"] for r in done if r.get("http_status") == 200 and _is_num(r.get("latency_ms"))]
    return {
        "calls": len(reserves) + sum(1 for r in done if r.get("kind") is None),
        "http_error_rows": sum(1 for r in done if r.get("http_status") is not None and r.get("http_status") != 200),
        "transport_error_rows": sum(1 for r in done if r.get("http_status") is None),
        "tokens": tokens,
        "total_cost_usd": math.fsum(r["cost_usd"] for r in rows),
        "settled_actual_cost_usd": math.fsum(actual),
        "worst_case_settled_count": worst_n,
        "worst_case_settled_cost_usd": math.fsum(worst_settled),
        "reserved_unsettled_count": len(unsettled),
        "reserved_unsettled_cost_usd": math.fsum(unsettled),
        "median_latency_ms_200": statistics.median(lat200) if lat200 else None,
    }


# ---------------------------------------------------------------- transport

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would re-send the x-api-key header to the new location."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_post(url: str, headers: dict, body: bytes, timeout: float):
    """POST once. Returns (status, lower-cased response headers, raw body) for ANY status, including 4xx/5xx and 3xx.
    Raises TimeoutError on a timeout, ConnectionError on any other failure to get a response. The original exception is
    chained away so no request header can reach a traceback or a message. Tests replace this function."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    except TimeoutError:
        raise TimeoutError("timeout") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TimeoutError("timeout") from None
        raise ConnectionError("connection failed") from None
    except (OSError, http.client.HTTPException):
        raise ConnectionError("connection failed") from None


# ---------------------------------------------------------------- caller

@dataclass
class CallResult:
    response: dict
    text: str
    usage: dict
    cost_usd: float
    spent_after: float
    budget_usd: float
    latency_ms: int
    request_sha256: str
    response_sha256: str | None
    http_status: int | None
    request_id: str | None
    attempts: int
    replayed: bool


@dataclass
class _Outcome:
    status: int | None
    headers: dict
    response: dict | None
    error_class: str | None
    error_type: str | None
    cost_usd: float
    spent_after: float
    latency_ms: int
    response_sha256: str | None
    request_id: str | None

    @property
    def ok(self) -> bool:
        return self.error_class is None

    @property
    def retryable(self) -> bool:
        return self.error_class in ("Timeout", "NetworkError") or self.status in RETRY_STATUSES


def _text_of(response: dict) -> str:
    content = response.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))


class HaikuCaller:
    """One ledger, one budget, one optional text store. Holds no key: the key is read from the environment per call."""

    def __init__(self, ledger, budget_usd, *, save_dir=None, model: str = DEFAULT_MODEL, allow_model=None, prices=None,
                 replay: bool = False, replay_only: bool = False, sleep=time.sleep):
        if ledger is None or str(ledger) == "":
            raise BadArguments("a ledger path is required")
        if not _is_num(budget_usd) or budget_usd < 0:
            raise BadArguments("budget_usd is required and must be a finite number >= 0")
        self.ledger = Ledger(ledger)
        self.budget_usd = float(budget_usd)
        self.model = model
        self.allow_model = allow_model
        self.prices, self.custom_prices = resolve_prices(model, allow_model, prices)
        self.replay_only = bool(replay_only)
        self.replay = bool(replay) or self.replay_only
        self.save_dir = Path(save_dir) if save_dir is not None else None
        if self.replay and self.save_dir is None:
            raise BadArguments("replay needs a save_dir (the text store is the replay cache)")
        self._sleep = sleep
        if self.save_dir is not None:
            self._ensure_save_dir()

    # -- text store

    def _ensure_save_dir(self) -> None:
        d = self.save_dir
        try:
            if not d.is_dir():
                d.parent.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(FileExistsError):  # another process may create it first
                    os.mkdir(d, 0o700)
            if not os.access(d, os.W_OK | os.X_OK):
                raise OSError
        except OSError:
            raise BadArguments("save_dir cannot be created or is not writable") from None

    def _store_path(self, sha: str) -> Path:
        return self.save_dir / f"{sha}.json"

    def _save(self, sha: str, body: dict, response: dict) -> None:
        data = json.dumps({"request": body, "response": response}, sort_keys=True, separators=(",", ":"), allow_nan=False)
        try:
            _write_private_atomic(self._store_path(sha), data.encode("utf-8"))
        except OSError:
            raise BadArguments("the call was made and recorded in the ledger, but the text store could not be written") from None

    def _load_saved(self, sha: str):
        path = self._store_path(sha)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise ReplayCorrupt("saved entry cannot be read") from None
        try:
            entry = json.loads(raw)
            ok = (isinstance(entry, dict) and isinstance(entry.get("response"), dict) and isinstance(entry.get("request"), dict)
                  and sha256_hex(canonical_bytes(entry["request"])) == sha)
        except (ValueError, BadArguments):
            ok = False
        if not ok:
            raise ReplayCorrupt("saved entry is unreadable or does not match its request hash")
        return entry["response"]

    # -- the call

    @staticmethod
    def _api_key() -> str:
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise MissingKey(f"{KEY_ENV} is not set or is empty")
        if not _KEY_OK.match(key):
            raise MissingKey(f"{KEY_ENV} has characters that cannot be sent in a header")
        return key

    def _build_body(self, system, messages, max_tokens, temperature, thinking) -> dict:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise BadArguments("max_tokens is required and must be an integer >= 1")
        if not isinstance(messages, list) or not messages:
            raise BadArguments("messages must be a non-empty list")
        if system is not None and not isinstance(system, (str, list)):
            raise BadArguments("system must be a string or a list of blocks")
        if temperature is not None and (not _is_num(temperature) or not 0 <= temperature <= 1):
            raise BadArguments("temperature must be a number in [0, 1]")
        if thinking is not None and not isinstance(thinking, dict):
            raise BadArguments("thinking must be a dict, or None to omit it")
        body = {"model": self.model, "max_tokens": max_tokens, "messages": copy.deepcopy(messages)}
        if system is not None:
            body["system"] = copy.deepcopy(system)
        if temperature is not None:
            body["temperature"] = temperature
        if thinking is not None:
            body["thinking"] = copy.deepcopy(thinking)
        return body

    def call(self, *, messages, max_tokens, system=None, temperature=None, thinking=DEFAULT_THINKING) -> CallResult:
        body = self._build_body(system, messages, max_tokens, temperature, thinking)
        body_bytes = canonical_bytes(body)
        sha = sha256_hex(body_bytes)

        if self.replay:
            saved = self._load_saved(sha)
            if saved is not None:
                with self.ledger.locked(exclusive=False):
                    spent = self.ledger.spent()
                counts = usage_counts(saved.get("usage"))
                return CallResult(response=saved, text=_text_of(saved), usage=counts, cost_usd=0.0, spent_after=spent,
                                  budget_usd=self.budget_usd, latency_ms=0, request_sha256=sha, response_sha256=None,
                                  http_status=None, request_id=None, attempts=0, replayed=True)
            if self.replay_only:
                raise ReplayMiss("no saved response for this request (replay_only)")

        key = self._api_key()
        last: _Outcome | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            out = self._attempt(attempt, key, body, body_bytes, sha, system, messages, max_tokens, thinking)
            if out.ok:
                if self.save_dir is not None:
                    self._save(sha, body, out.response)
                counts = usage_counts(out.response.get("usage"))
                return CallResult(response=out.response, text=_text_of(out.response), usage=counts, cost_usd=out.cost_usd,
                                  spent_after=out.spent_after, budget_usd=self.budget_usd, latency_ms=out.latency_ms,
                                  request_sha256=sha, response_sha256=out.response_sha256, http_status=out.status,
                                  request_id=out.request_id, attempts=attempt, replayed=False)
            if not out.retryable:
                if out.status is not None:
                    raise ApiError(f"API error: status={out.status} type={out.error_type}", status=out.status, error_type=out.error_type)
                raise ApiError(f"API error: {out.error_class}", status=None, error_type=out.error_type)
            last = out
            if attempt < MAX_ATTEMPTS:
                self._sleep(_retry_delay(attempt, out.headers))
        raise RetriesExhausted(
            f"retries exhausted after {MAX_ATTEMPTS} attempts (last: status={last.status} class={last.error_class})",
            attempts=MAX_ATTEMPTS, last_status=last.status, last_error_class=last.error_class)

    def _attempt(self, attempt, key, body, body_bytes, sha, system, messages, max_tokens, thinking) -> _Outcome:
        """One budget-checked HTTP attempt, reserved then settled in the ledger, all under the exclusive ledger lock.

        Order: check the budget; append a "reserve" row whose cost_usd is this attempt's worst case; only then send the
        request; append a "settle" row whose cost_usd is (what the attempt really cost - the reserved worst case). With no
        usable response (timeout, dropped connection, any exception from the transport, a 200 whose body cannot be read)
        the settle row's cost_usd is 0.0, so the worst case stays charged. If the process dies between the two rows the
        reserve row stays alone and the worst case stays charged, which is intended."""
        request_headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        worst = worst_case_cost(system, messages, max_tokens, self.prices)
        with self.ledger.locked(exclusive=True):
            spent = self.ledger.spent()
            if not (spent + worst <= self.budget_usd):  # also refuses if anything is NaN
                raise BudgetExceeded(f"budget exceeded: spent={spent:.8f} + worst_case={worst:.8f} > budget={self.budget_usd:.8f} USD; no call made")
            fd = self.ledger.open_append()
            try:
                reservation_id = uuid.uuid4().hex
                Ledger.append_row(fd, {  # durable before the request can leave the machine; if this fails, nothing is sent
                    "kind": "reserve", "ts": _utc_now(), "reservation_id": reservation_id, "model": self.model,
                    "request_sha256": sha, "attempt": attempt, "cost_usd": worst})

                # From here a request may be on the wire. Anything that stops us before the settle row leaves `worst` charged.
                t0 = time.monotonic()
                status, headers, raw, error_class = None, {}, None, None
                try:
                    status, headers, raw = _http_post(API_URL, request_headers, body_bytes, TIMEOUT_S)
                    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
                except TimeoutError:
                    error_class = "Timeout"
                except (OSError, http.client.HTTPException):
                    error_class = "NetworkError"
                except Exception:
                    error_class = "TransportError"
                latency_ms = int(round((time.monotonic() - t0) * 1000))

                resp_json = None
                if raw is not None:
                    try:
                        parsed = json.loads(raw)
                        resp_json = parsed if isinstance(parsed, dict) else None
                    except (ValueError, TypeError):
                        resp_json = None
                if error_class is None:
                    if status != 200:
                        error_class, cost_basis = "HTTPError", "http_error"  # an error response is not billed: settles to 0
                    elif resp_json is None:
                        error_class, cost_basis = "MalformedResponse", "worst_case_unreadable_200"  # billed, usage unknown
                    else:
                        cost_basis = "usage"
                else:
                    cost_basis = "worst_case_no_response"
                error_type = _server_error_type(resp_json) if error_class in ("HTTPError", "MalformedResponse") else None

                if cost_basis == "usage":
                    counts = usage_counts(resp_json.get("usage"))
                    charged = cost_from_usage(counts, self.prices)
                else:
                    counts = usage_counts(None)
                    charged = 0.0 if cost_basis == "http_error" else worst
                settle_delta = 0.0 if cost_basis.startswith("worst_case") else charged - worst
                request_id = _safe_token(headers.get("request-id"))
                stop_reason = _safe_token(resp_json.get("stop_reason")) if cost_basis == "usage" else None
                response_sha = sha256_hex(raw) if isinstance(raw, (bytes, bytearray)) else None
                http_status = status if isinstance(status, int) and not isinstance(status, bool) else None
                Ledger.append_row(fd, {
                    "kind": "settle", "ts": _utc_now(), "reservation_id": reservation_id, "model": self.model,
                    "request_sha256": sha, "response_sha256": response_sha, "http_status": http_status,
                    "request_id": request_id, "stop_reason": stop_reason, **counts, "cost_usd": settle_delta,
                    "cost_basis": cost_basis, "latency_ms": latency_ms, "attempt": attempt,
                    "thinking": copy.deepcopy(thinking), "allow_model": self.allow_model,
                    "custom_prices": dict(self.prices) if self.custom_prices else None,
                    "error_class": error_class, "error_type": error_type})
            finally:
                os.close(fd)
        return _Outcome(status=http_status, headers=headers, response=resp_json, error_class=error_class,
                        error_type=error_type, cost_usd=charged, spent_after=spent + charged, latency_ms=latency_ms,
                        response_sha256=response_sha, request_id=request_id)


# ---------------------------------------------------------------- CLI

def _load_request(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            req = json.loads(f.read())
    except (OSError, ValueError):
        raise BadArguments("--request file cannot be read as JSON") from None
    if not isinstance(req, dict):
        raise BadArguments("--request file must hold a JSON object")
    extra = sorted(set(req) - REQUEST_KEYS)
    if extra:
        raise BadArguments("--request has keys that are not accepted: " + ", ".join(extra[:8])
                           + " (accepted: " + ", ".join(sorted(REQUEST_KEYS)) + ")")
    if "max_tokens" not in req or "messages" not in req:
        raise BadArguments("--request needs max_tokens and messages")
    kw = {k: req[k] for k in ("messages", "max_tokens", "system", "temperature") if k in req}
    if "thinking" in req:  # absent -> the default (disabled); an explicit null omits the parameter
        kw["thinking"] = req["thinking"]
    return kw


def format_summary(result: CallResult) -> str:
    status = "replay" if result.replayed else result.http_status
    return (f"status={status} input_tokens={result.usage['input_tokens']} output_tokens={result.usage['output_tokens']} "
            f"cost_usd={result.cost_usd:.8f} spent_after={result.spent_after:.8f} budget_usd={result.budget_usd:.4f} "
            f"latency_ms={result.latency_ms}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.haiku_caller", description=(__doc__ or "").split("\n")[0])
    p.add_argument("--request", required=True, metavar="FILE", help="JSON: system, messages, max_tokens, optional temperature, thinking")
    p.add_argument("--out", required=True, metavar="FILE", help="response JSON is written here, mode 600")
    p.add_argument("--ledger", required=True, metavar="FILE", help="JSONL ledger; its cost_usd sum is the spend so far")
    p.add_argument("--budget-usd", required=True, type=float, help="hard cap on the ledger's total cost_usd")
    p.add_argument("--save-dir", metavar="DIR", help="text store and replay cache (dir 700, files 600)")
    p.add_argument("--replay", action="store_true", help="use a saved response when one exists, else call")
    p.add_argument("--replay-only", action="store_true", help="never call: saved response or exit 4")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--allow-model", metavar="ID", help="must equal --model when it is not the default")
    p.add_argument("--prices-json", metavar="JSON", help='per-million prices for a non-default model: {"input":..,"cache_write":..,"cache_read":..,"output":..}')
    return p


def main(argv=None, *, sleep=time.sleep) -> int:
    try:
        ns = _build_parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_BAD_ARGS
    try:
        kw = _load_request(ns.request)
        prices = None
        if ns.prices_json is not None:
            try:
                prices = json.loads(ns.prices_json)
            except ValueError:
                raise BadArguments("--prices-json is not valid JSON") from None
        out_dir = Path(ns.out).resolve().parent
        if not out_dir.is_dir() or not os.access(out_dir, os.W_OK):
            raise BadArguments("--out directory does not exist or is not writable")
        caller = HaikuCaller(ns.ledger, ns.budget_usd, save_dir=ns.save_dir, model=ns.model, allow_model=ns.allow_model,
                             prices=prices, replay=ns.replay, replay_only=ns.replay_only, sleep=sleep)
        result = caller.call(**kw)
        try:
            _write_private(ns.out, (json.dumps(result.response, indent=2) + "\n").encode("utf-8"))
        except OSError:
            raise BadArguments("the call was made and recorded in the ledger, but --out could not be written") from None
        print(format_summary(result))
        return EXIT_OK
    except HaikuCallerError as e:
        print(f"haiku_caller: {e}", file=sys.stderr)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
