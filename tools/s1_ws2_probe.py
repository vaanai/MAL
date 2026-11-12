"""Screen and probe candidate SECOND websocket providers for s1 (DEC-031 Amendment 22 item 10 names a keyed Helius websocket; job #1891 measured it at
4,850.3 MB/h, about 97,007 credits/h, about 58.7M credits to 2026-12-01). PAPER ONLY. Counts, bytes and timings only: no notification content is kept or printed.

    /var/lib/mal/fast-listener/.venv/bin/python -I tools/s1_ws2_probe.py --url wss://a --url wss://b/path [--ref-key-file /var/lib/mal/fast-listener/helius.env]

Every socket opens the subscription S*'s shadow opens (logsSubscribe, mentions the PumpSwap program, commitment confirmed: tools/q50s1_ws_probe.subscribe_request,
tested equal to S*'s builder) with the websockets defaults S* uses (permessage-deflate offered, ping 20 s, open 20 s, max_size 8 MB, a 30 s idle ends the connection,
backoff 1 s doubling to 60 s with 50-100 % jitter). Phases, one JSON line each on stdout:
 1. baseline: no probe socket, host watch only, --baseline-seconds.
 2. screen: each --url alone for --screen-seconds, then --gap-seconds of watch only.
 3. probe: the --top screened candidates (subscription acknowledged, notifications received; ranked by coverage of the reference, else by notifications) each for
    --probe-seconds, then --gap-seconds of watch only.
 4. after: watch only, --baseline-seconds.
Reference: with --ref-key-file, a keyed Helius socket (same subscription) runs during every candidate window; the key is read inside this process (never argv, never
printed; exception text is never printed, only its class). Coverage = share of the reference's signatures first seen in the window's middle (15 s trimmed each end)
that the candidate also delivered; lead = candidate receive time minus reference receive time (ms) on common signatures. Signatures are kept in memory only, cut
to 24 characters, and dropped at the end of each window.
Host watch: every --watch-every seconds `ss -Htni state established`, host-wide; TCP connections to port 443 of --watch-host's addresses (default
api.mainnet-beta.solana.com, re-resolved each minute) are counted per phase: mean open, new and ended connections per hour, ended long-lived connections (seen in
>= 3 samples) per hour, and bytes_received per hour. No process is named or attributed. The probe's own sockets' bytes_received (compressed wire bytes) come from
the same samples.
Candidate URLs must be key-free wss:// URLs (no query, fragment, userinfo or key-like token). One KEYED candidate may be added with --keyed-url BASE --key-file F
--key-var VAR [--key-param P]: the key is read from F inside this process and appended as ?P=<key> (the query form S* strips from its logs); only BASE is ever printed,
provider error text is not kept for it, and a HELIUS_API_KEY is refused for any host other than *.helius-rpc.com (no key goes to another provider).
Run it as a MiScusi job on mal-fast-0 (websockets + certifi are in the fast-listener venv); 512 MB.
Exit: 0 ran (any outcome), 2 usage.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.q50s1_ws_probe import read_key, subscribe_request  # noqa: E402  (shared with the Helius credit probe; -I drops the repo from sys.path)

REF_BASE = "wss://mainnet.helius-rpc.com/"
KEYLIKE = re.compile(r"(?i)api[-_]?key|[?&](key|token|auth|apikey)=|://[^/\s]*@")  # scripts/ops/q50s1-ws-launch.py:KEYLIKE
GAP_SLOTS = 30  # S* tools/h5_shadow.py:241
SILENCE_S = 20.0  # S* tools/h5_shadow.py:242
IDLE_S = 30.0  # S* observe/trade_source.py _iter_ws recv timeout
INITIAL_BACKOFF_S, MAX_BACKOFF_S = 1.0, 60.0  # S* observe/trade_source.py:52-53
TRIM_MS = 15_000  # at most; 15 % of a shorter window
SIG_KEEP = 24
BYTES_RE = re.compile(r"\bbytes_received:(\d+)")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")  # scripts/ops/q50s1-ws-launch.py:KEY_RE (a key this probe accepts, the launcher accepts)


def url_ok(u: str) -> bool:
    return u.startswith("wss://") and len(u) > 6 and not any(c in u for c in "?#@ ") and not KEYLIKE.search(u)


def read_var(path: str, var: str) -> str | None:
    """The value of `var` in a KEY=VALUE file (optional `export `, optional quotes); None if unreadable, absent or malformed. Never logs."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for ln in lines:
        t = ln.strip()
        if t.startswith("export "):
            t = t[7:].lstrip()
        if t.startswith(var + "="):
            v = t[len(var) + 1:].strip().strip("'\"")
            return v if KEY_RE.match(v) else None
    return None


def keyed_ok(base: str, var: str, param: str) -> bool:
    """A key-free base, sane names, and the Helius key only ever to a Helius host."""
    if not (url_ok(base) and NAME_RE.match(var) and NAME_RE.match(param)):
        return False
    host = base[6:].split("/", 1)[0].lower()
    return var != "HELIUS_API_KEY" or host == "helius-rpc.com" or host.endswith(".helius-rpc.com")


def with_key(base: str, param: str, key: str) -> str:
    return f"{base}?{param}={key}"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def split_addr(a: str) -> tuple[str, str]:
    """'1.2.3.4:443' / '[::ffff:1.2.3.4]:443' / '[2001:db8::1]:443' / '[fe80::1%eth0]:443' -> (ip, port)."""
    host, _, port = a.rpartition(":")
    host = host.strip("[]").split("%", 1)[0]
    if host.lower().startswith("::ffff:") and "." in host:
        host = host[7:]
    return host, port


def parse_ss(text: str) -> dict[tuple[str, str], int | None]:
    """`ss -Htni state established` -> {(local, peer): bytes_received or None}. A connection line is followed by an indented info line."""
    out: dict[tuple[str, str], int | None] = {}
    key = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t":
            if key is not None:
                m = BYTES_RE.search(line)
                if m:
                    out[key] = int(m.group(1))
            continue
        parts = line.split()
        if len(parts) < 4:
            key = None
            continue
        key = (parts[-2], parts[-1])
        out[key] = None
    return out


def pct(xs: list[int], q: float) -> int | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


class Stream:
    def __init__(self, name: str, keep_detail: bool) -> None:
        self.name = name
        self.keep_detail = keep_detail  # False for the keyed reference: no provider error text is kept
        self.connects = self.reconnects = self.msgs = self.notes = self.payload_bytes = 0
        self.bad_json = self.rpc_errors = self.err_notes = self.slot_jumps = self.max_slot_jump = self.silences = self.idle_breaks = 0
        self.rejections: dict[str, int] = {}
        self.closes: dict[str, int] = {}
        self.errors: dict[str, int] = {}
        self.sub_ok = False
        self.sub_ack_ms: int | None = None
        self.sub_error: str | None = None
        self.extensions: list[str] = []
        self.first_note_ms: int | None = None
        self.last_note_ms: int | None = None
        self.last_slot: int | None = None
        self.up_since: int | None = None
        self.up_ms = 0
        self.local_ports: set[str] = set()
        self.sigs: dict[str, int] = {}
        self.frozen = False  # set at the window's end: nothing after it is counted

    def freeze(self, t: int) -> None:
        self.mark_down(t)
        self.frozen = True

    def mark_up(self, t: int) -> None:
        if self.up_since is None and not self.frozen:
            self.up_since = t

    def mark_down(self, t: int) -> None:
        if self.up_since is not None:
            self.up_ms += t - self.up_since
            self.up_since = None

    def note(self, msg: dict, t: int) -> None:
        if self.frozen:
            return
        if msg.get("method") is None:
            if "result" in msg and msg.get("id") == 1:
                self.sub_ok = True
                self.mark_up(t)
            elif "error" in msg:
                self.rpc_errors += 1
                err = msg.get("error")
                if self.sub_error is None:
                    code = err.get("code") if isinstance(err, dict) else None
                    text = str(err.get("message", ""))[:160] if (isinstance(err, dict) and self.keep_detail) else ""
                    self.sub_error = f"{code} {text}".strip()
            return
        res = (msg.get("params") or {}).get("result") or {}
        ctx = res.get("context") or {}
        val = res.get("value") or {}
        self.notes += 1
        self.mark_up(t)
        if self.first_note_ms is None:
            self.first_note_ms = t
        if self.last_note_ms is not None and t - self.last_note_ms > SILENCE_S * 1000:
            self.silences += 1
        self.last_note_ms = t
        if val.get("err") is not None:
            self.err_notes += 1
        slot = ctx.get("slot")
        if isinstance(slot, int):
            if self.last_slot is not None and slot > self.last_slot + GAP_SLOTS:
                self.slot_jumps += 1
                self.max_slot_jump = max(self.max_slot_jump, slot - self.last_slot)
            if self.last_slot is None or slot > self.last_slot:
                self.last_slot = slot
        sig = val.get("signature")
        if isinstance(sig, str):
            self.sigs.setdefault(sig[:SIG_KEEP], t)


def _bump(d: dict[str, int], k) -> None:
    d[str(k)] = d.get(str(k), 0) + 1


async def run_stream(url: str, st: Stream, stop: asyncio.Event, rng: random.Random) -> None:
    import ssl

    import certifi
    import websockets

    ctx = ssl.create_default_context(cafile=certifi.where())
    backoff = INITIAL_BACKOFF_S
    while not stop.is_set():
        try:
            async with websockets.connect(url, ssl=ctx, ping_interval=20, ping_timeout=20, close_timeout=5, open_timeout=20, max_size=8_000_000) as ws:
                st.connects += 1
                la = getattr(ws, "local_address", None)
                if la:
                    st.local_ports.add(str(la[1]))
                exts = getattr(ws, "extensions", None)
                if exts is None:
                    exts = getattr(getattr(ws, "protocol", None), "extensions", None) or []
                for e in exts:
                    nm = getattr(e, "name", type(e).__name__)
                    if nm not in st.extensions:
                        st.extensions.append(nm)
                await ws.send(json.dumps(subscribe_request()))
                t_sub = now_ms()
                last = time.monotonic()
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        if time.monotonic() - last >= IDLE_S:
                            st.idle_breaks += 1
                            break
                        continue
                    if st.frozen:
                        break
                    last = time.monotonic()
                    t = now_ms()
                    st.msgs += 1
                    st.payload_bytes += len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))
                    try:
                        msg = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        st.bad_json += 1
                        continue
                    if isinstance(msg, dict):
                        was = st.sub_ok
                        st.note(msg, t)
                        if st.sub_ok and not was:
                            st.sub_ack_ms = t - t_sub
            if stop.is_set():
                break
            st.mark_down(now_ms())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the text may name the URL (and the reference's key), so only the class and a status/close code are kept
            st.mark_down(now_ms())
            code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            rcvd = getattr(exc, "rcvd", None)
            if code is not None:
                _bump(st.rejections, code)
            elif rcvd is not None or type(exc).__name__.startswith("ConnectionClosed"):
                _bump(st.closes, rcvd.code if rcvd is not None else 1006)
            else:
                _bump(st.errors, type(exc).__name__)
        if stop.is_set():
            break
        st.reconnects += 1
        delay = backoff * (0.5 + 0.5 * rng.random())
        backoff = min(backoff * 2, MAX_BACKOFF_S)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
    st.mark_down(now_ms())


class Watcher:
    def __init__(self, host: str, every: float) -> None:
        self.host = host
        self.every = every
        self.ips: set[str] = set()
        self.phase = "init"
        self.prev: dict[tuple[str, str], int | None] = {}
        self.age: dict[tuple[str, str], int] = {}
        self.stats: dict[str, dict] = {}
        self.own_ports: set[str] = set()
        self.own_bytes: dict[tuple[str, str], int] = {}
        self.resolved_at = 0.0
        self.ss_errors = 0
        self.started = False

    def resolve(self) -> None:
        try:
            for fam, _, _, _, sa in socket.getaddrinfo(self.host, 443, proto=socket.IPPROTO_TCP):
                self.ips.add(split_addr(f"[{sa[0]}]:{sa[1]}")[0])
        except OSError:
            pass
        self.resolved_at = time.monotonic()

    def update(self, conns: dict[tuple[str, str], int | None], dt: float) -> None:
        s = self.stats.setdefault(self.phase, {"samples": 0, "seconds": 0.0, "open_sum": 0, "new": 0, "ended": 0, "ended_long": 0, "bytes": 0})
        mine = {k: v for k, v in conns.items() if split_addr(k[1])[1] == "443" and split_addr(k[1])[0] in self.ips}
        for k, v in conns.items():
            if split_addr(k[0])[1] in self.own_ports and v is not None:
                self.own_bytes[k] = max(self.own_bytes.get(k, 0), v)
        s["samples"] += 1
        s["seconds"] += dt
        s["open_sum"] += len(mine)
        if self.started:
            for k in mine:
                if k not in self.age:
                    s["new"] += 1
            for k in list(self.age):
                if k not in mine:
                    s["ended"] += 1
                    if self.age[k] >= 3:
                        s["ended_long"] += 1
        for k, v in mine.items():
            pv = self.prev.get(k)
            if v is not None and pv is not None and v >= pv:
                s["bytes"] += v - pv
        self.age = {k: self.age.get(k, 0) + 1 for k in mine}
        self.prev = mine
        self.started = True

    async def run(self, stop: asyncio.Event) -> None:
        self.resolve()
        last = time.monotonic()
        while not stop.is_set():
            if time.monotonic() - self.resolved_at >= 60:
                await asyncio.get_running_loop().run_in_executor(None, self.resolve)
            try:
                p = await asyncio.create_subprocess_exec("ss", "-Htni", "state", "established", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await p.communicate()
                t = time.monotonic()
                self.update(parse_ss(out.decode("utf-8", "replace")), t - last)
                last = t
            except (OSError, ValueError):
                self.ss_errors += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.every)
            except asyncio.TimeoutError:
                pass

    def summary(self) -> dict:
        out = {}
        for ph, s in self.stats.items():
            h = max(s["seconds"], 1e-9) / 3600.0
            out[ph] = {"samples": s["samples"], "seconds": round(s["seconds"], 1), "mean_open": round(s["open_sum"] / max(s["samples"], 1), 2),
                       "new_per_h": round(s["new"] / h, 1), "ended_per_h": round(s["ended"] / h, 1), "ended_long_per_h": round(s["ended_long"] / h, 1),
                       "ended_long": s["ended_long"], "mb_received_per_h": round(s["bytes"] / 1e6 / h, 1)}
        return out


def stream_summary(st: Stream, seconds: float, t_end: int, t_start: int | None = None) -> dict:
    st.mark_down(t_end)
    h = max(seconds, 1e-9) / 3600.0
    return {"connects": st.connects, "reconnects": st.reconnects, "rejections": st.rejections, "closes": st.closes, "errors": st.errors,
            "sub_ok": st.sub_ok, "sub_ack_ms": st.sub_ack_ms, "sub_error": st.sub_error, "extensions": st.extensions,
            "msgs": st.msgs, "notes": st.notes, "notes_per_s": round(st.notes / max(seconds, 1e-9), 1), "payload_bytes": st.payload_bytes,
            "payload_mb_per_h": round(st.payload_bytes / 1e6 / h, 1), "up_share": round(st.up_ms / max(seconds * 1000, 1), 4),
            "first_note_s": round((st.first_note_ms - t_start) / 1000.0, 1) if (st.first_note_ms is not None and t_start is not None) else None,
            "bad_json": st.bad_json, "rpc_errors": st.rpc_errors, "err_note_share": round(st.err_notes / max(st.notes, 1), 4),
            f"slot_jumps_gt{GAP_SLOTS}": st.slot_jumps, "max_slot_jump": st.max_slot_jump, f"silences_gt{int(SILENCE_S)}s": st.silences, "idle_breaks": st.idle_breaks}


def coverage(cand: Stream, ref: Stream, t0: int, t1: int) -> dict:
    trim = min(TRIM_MS, int(0.15 * (t1 - t0)))
    lo, hi = t0 + trim, t1 - trim
    ref_mid = [s for s, t in ref.sigs.items() if lo <= t <= hi]
    cand_mid = [s for s, t in cand.sigs.items() if lo <= t <= hi]
    common = [s for s in ref_mid if s in cand.sigs]
    leads = [cand.sigs[s] - ref.sigs[s] for s in common]
    extra = sum(1 for s in cand_mid if s not in ref.sigs)
    return {"ref_sigs_mid": len(ref_mid), "cand_has": len(common), "coverage": round(len(common) / len(ref_mid), 4) if ref_mid else None,
            "cand_sigs_mid": len(cand_mid), "cand_extra_share": round(extra / len(cand_mid), 4) if cand_mid else None,
            "lead_ms_p10": pct(leads, 0.10), "lead_ms_p50": pct(leads, 0.50), "lead_ms_p90": pct(leads, 0.90),
            "cand_first_share": round(sum(1 for x in leads if x < 0) / len(leads), 4) if leads else None}


async def window(phase: str, label: str, url: str, seconds: float, ref_url: str | None, watcher: Watcher, rng: random.Random, *, shown: str | None = None,
                 keep_detail: bool = True) -> dict:
    """`url` is what is opened; `shown` (default url) is what is printed: for the keyed candidate the key-free base."""
    watcher.phase = f"{phase}:{label}"
    stop = asyncio.Event()
    cand = Stream(label, keep_detail=keep_detail)
    ref = Stream("helius-ref", keep_detail=False) if ref_url else None
    t0 = now_ms()
    tasks = [asyncio.create_task(run_stream(url, cand, stop, rng))]
    if ref is not None:
        tasks.append(asyncio.create_task(run_stream(ref_url, ref, stop, rng)))
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        watcher.own_ports = cand.local_ports | (ref.local_ports if ref else set())
        await asyncio.sleep(min(1.0, max(0.0, end - time.monotonic())))
    t1 = now_ms()  # the window's end is when stop is set; the close handshakes after it are not counted as window time
    cand.freeze(t1)
    if ref is not None:
        ref.freeze(t1)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    secs = (t1 - t0) / 1000.0
    rec = {"type": "window", "phase": phase, "label": label, "url": shown or url, "keyed": not keep_detail, "seconds": round(secs, 1), "cand": stream_summary(cand, secs, t1, t0)}
    rec["cand"]["wire_bytes_sampled"] = sum(v for k, v in watcher.own_bytes.items() if split_addr(k[0])[1] in cand.local_ports)
    if ref is not None:
        rec["ref"] = stream_summary(ref, secs, t1, t0)
        rec["ref"]["wire_bytes_sampled"] = sum(v for k, v in watcher.own_bytes.items() if split_addr(k[0])[1] in ref.local_ports)
        rec["ref"]["credits_at_2_per_0.1MB"] = round(ref.payload_bytes / 1e5 * 2, 1)
        rec["coverage_vs_ref"] = coverage(cand, ref, t0, t1)
        rec["notes_ratio_vs_ref"] = round(cand.notes / ref.notes, 4) if ref.notes else None
    cand.sigs.clear()
    if ref is not None:
        ref.sigs.clear()
    print(json.dumps(rec, sort_keys=True), flush=True)
    return rec


async def watch_only(phase: str, seconds: float, watcher: Watcher) -> None:
    watcher.phase = phase
    await asyncio.sleep(seconds)


def rank(screens: list[dict]) -> list[dict]:
    ok = [r for r in screens if r["cand"]["sub_ok"] and r["cand"]["notes"] > 0]
    return sorted(ok, key=lambda r: (-(r.get("coverage_vs_ref", {}).get("coverage") or 0.0), -r["cand"]["notes"], r["cand"]["reconnects"]))


async def amain(a, ref_url: str | None, keyed: tuple[str, str] | None = None) -> dict:
    """keyed = (key-free base, URL with the key) for the one keyed candidate."""
    rng = random.Random(1)
    watcher = Watcher(a.watch_host, a.watch_every)
    wstop = asyncio.Event()
    wtask = asyncio.create_task(watcher.run(wstop))
    cands = [(u, u, True) for u in a.url] + ([(keyed[0], keyed[1], False)] if keyed else [])
    real = {shown: (u, keep) for shown, u, keep in cands}
    dns = {}
    for u, _, _ in cands:
        host = u[6:].split("/", 1)[0]
        try:
            dns[u] = len({x[4][0] for x in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
        except OSError:
            dns[u] = 0
    print(json.dumps({"type": "dns", "addresses": dns}, sort_keys=True), flush=True)
    await watch_only("baseline", a.baseline_seconds, watcher)
    screens = []
    for i, (u, _, _) in enumerate(cands):
        if dns[u] == 0:
            continue
        screens.append(await window("screen", f"c{i}", real[u][0], a.screen_seconds, ref_url, watcher, rng, shown=u, keep_detail=real[u][1]))
        await watch_only(f"gap:screen:c{i}", a.gap_seconds, watcher)
    probes = []
    for r in rank(screens)[: a.top]:
        probes.append(await window("probe", r["label"], real[r["url"]][0], a.probe_seconds, ref_url, watcher, rng, shown=r["url"], keep_detail=real[r["url"]][1]))
        await watch_only(f"gap:probe:{r['label']}", a.gap_seconds, watcher)
    await watch_only("after", a.baseline_seconds, watcher)
    wstop.set()
    await wtask
    return {"type": "summary", "tool": "s1_ws2_probe", "subscription": "logsSubscribe mentions PumpSwap, commitment confirmed", "dns": dns,
            "watch_host": a.watch_host, "watch_ips": len(watcher.ips), "watch": watcher.summary(), "ss_errors": watcher.ss_errors,
            "ref": "helius" if ref_url else None, "screens": screens, "probes": probes,
            "ref_credits_at_2_per_0.1MB_total": round(sum(w.get("ref", {}).get("credits_at_2_per_0.1MB", 0) for w in screens + probes), 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", action="append", default=[], help="key-free candidate wss:// URL; repeatable")
    ap.add_argument("--keyed-url", default=None, help="key-free base of ONE keyed candidate (the key is appended in-process as ?<key-param>=<key>)")
    ap.add_argument("--key-file", default=None, help="KEY=VALUE file holding the keyed candidate's key")
    ap.add_argument("--key-var", default=None, help="variable name of the keyed candidate's key in --key-file")
    ap.add_argument("--key-param", default="api_key", help="query parameter name the provider expects (default api_key)")
    ap.add_argument("--ref-key-file", default=None, help="KEY=VALUE file with HELIUS_API_KEY for the reference socket (read in-process)")
    ap.add_argument("--screen-seconds", type=float, default=45.0)
    ap.add_argument("--probe-seconds", type=float, default=600.0)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--baseline-seconds", type=float, default=300.0)
    ap.add_argument("--gap-seconds", type=float, default=90.0)
    ap.add_argument("--watch-host", default="api.mainnet-beta.solana.com")
    ap.add_argument("--watch-every", type=float, default=5.0)
    a = ap.parse_args(argv)
    bad = [u for u in a.url if not url_ok(u)]
    if (not a.url and not a.keyed_url) or bad or len(set(a.url)) != len(a.url) or (a.keyed_url in a.url):
        print(f"s1_ws2_probe: {len(bad)} URL(s) not key-free wss:// or duplicated; nothing opened", file=sys.stderr)
        return 2
    if not (10 <= a.screen_seconds <= 600 and 0 <= a.probe_seconds <= 3600 and 0 <= a.top <= 5 and 0 <= a.baseline_seconds <= 1800
            and 0 <= a.gap_seconds <= 600 and 1 <= a.watch_every <= 60 and len(a.url) <= 16):
        print("s1_ws2_probe: a duration, --top or the URL count is out of range", file=sys.stderr)
        return 2
    keyed = None
    if a.keyed_url or a.key_file or a.key_var:
        if not (a.keyed_url and a.key_file and a.key_var and keyed_ok(a.keyed_url, a.key_var, a.key_param)):
            print("s1_ws2_probe: --keyed-url, --key-file and --key-var go together; the base must be key-free wss://, the names [A-Za-z0-9_-], and a HELIUS_API_KEY"
                  " only for a helius-rpc.com host; nothing opened", file=sys.stderr)
            return 2
        k = read_var(a.key_file, a.key_var)
        if k is None:
            print("s1_ws2_probe: no usable value for --key-var in --key-file; nothing opened", file=sys.stderr)
            return 2
        keyed = (a.keyed_url, with_key(a.keyed_url, a.key_param, k))
        del k
    ref_url = None
    if a.ref_key_file:
        key = read_key(a.ref_key_file)
        if key is None:
            print("s1_ws2_probe: no usable HELIUS_API_KEY in --ref-key-file; nothing opened", file=sys.stderr)
            return 2
        ref_url = f"{REF_BASE}?api-key={key}"
        del key
    summ = asyncio.run(amain(a, ref_url, keyed))
    print(json.dumps(summ, sort_keys=True), flush=True)
    rp = os.environ.get("MISCUSI_RESULT")
    if rp:
        best = summ["probes"][0] if summ["probes"] else None
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump({"ok": True, "summary": f"s1_ws2_probe: {len(summ['screens'])} screened, {len(summ['probes'])} probed; best "
                       + (f"{best['url']} coverage {best.get('coverage_vs_ref', {}).get('coverage')}" if best else "none"),
                       "metrics": {"screened": len(summ["screens"]), "probed": len(summ["probes"]), "ref_credits": summ["ref_credits_at_2_per_0.1MB_total"]}}, fh)
    od = os.environ.get("MISCUSI_OUTPUT_DIR")
    if od:
        with open(os.path.join(od, "s1_ws2_probe.json"), "w", encoding="utf-8") as fh:
            json.dump(summ, fh, sort_keys=True, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
