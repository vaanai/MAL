#!/usr/bin/env python3
"""floor_tip_linkdir: build and keep ONE arena-layout tape dir for the FLOOR lane decision host on mal-fast-0 (tools/floor_lane_host.py tape_dir).

The arena tip writer's dir (SRC, ~25 h kept) must not be touched, and the FLOOR host needs >= 201 h of history plus the live open hour. This tool makes DST:
  * a HARD LINK of every writer-dir tape file (os.link; same filesystem). An open tape-H.jsonl shares the inode, so appends are visible in DST.
  * a closed hour (tape-H.jsonl.zst in SRC) is linked into DST BEFORE DST's tape-H.jsonl is unlinked (tools/tape_mirror.py layout rule), so a reader following
    the .jsonl continues in the .zst at the same decompressed offset.
  * hours older than RETAIN_H (by name) are deleted from DST only.
  * once at start, missing CLOSED hours (.jsonl.zst) in [now - BACKFILL_H, earliest SRC hour) are copied from research-0 over `ssh mal-research-0`
    (cat to <name>.part, sha256 compared with the remote `sha256sum`, then rename). Nothing is written under MIN_FREE_PCT free.
It never writes to SRC and never parses a tape row: names, sizes and sha256 only. DST/.linkdir/status.json holds hours, earliest, latest, counts, errors.
PAPER ONLY: no key, no wallet, no RPC.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HOUR_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"]
DEFAULT_REMOTE = "/data2/mal/arena-tip-feed/hv7"
CHUNK = 1 << 20


def hour_ts(h: str) -> int:
    return calendar.timegm(time.strptime(h, "%Y-%m-%dT%H"))


def hour_name(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ts))


def scan_names(names) -> dict:
    """{hour: {'jsonl': bool, 'zst': bool}} from names only."""
    out: dict = {}
    for n in names:
        m = HOUR_RE.match(n)
        if m:
            out.setdefault(m.group(1), {"jsonl": False, "zst": False})["zst" if m.group(2) else "jsonl"] = True
    return out


def scan(d: Path) -> dict:
    try:
        return scan_names(os.listdir(d))
    except FileNotFoundError:
        return {}


def free_pct(path: str) -> float:
    s = os.statvfs(path)
    return 100.0 * s.f_bavail / s.f_blocks if s.f_blocks else 0.0


class Linker:
    def __init__(self, src, dst, *, retain_h=240, backfill_h=230, min_free_pct=20, remote_host="mal-research-0", remote_dir=DEFAULT_REMOTE,
                 ssh_prefix=None, free_path="/", progress=None, now=time.time, dry=False):
        self.src, self.dst = Path(src), Path(dst)
        self.retain_h, self.backfill_h, self.min_free_pct = retain_h, backfill_h, min_free_pct
        self.remote_host, self.remote_dir = remote_host, remote_dir
        self.ssh = list(ssh_prefix) if ssh_prefix else ["ssh", *SSH_OPTS]
        self.free_path, self.progress, self.now, self.dry = free_path, progress, now, dry
        self.errors: list = []
        self.backfilled = 0
        self.last_sync = None
        self.meta = self.dst / ".linkdir"

    def err(self, msg: str) -> None:
        self.errors = (self.errors + [{"t": int(self.now()), "e": msg}])[-20:]

    # ---- sync ----
    def sync_once(self) -> dict:
        acts = {"link": 0, "close": 0, "prune": 0}
        if not self.dry:
            self.dst.mkdir(parents=True, exist_ok=True)
        s, d = scan(self.src), scan(self.dst)
        for h in sorted(s):
            sj, sz = f"tape-{h}.jsonl", f"tape-{h}.jsonl.zst"
            dh = d.get(h, {})
            try:
                if s[h]["zst"]:
                    if not dh.get("zst"):
                        self._link(sz, acts)
                    if dh.get("jsonl"):  # .zst first (above), only then the .jsonl goes
                        acts["close"] += 1
                        if not self.dry and (self.dst / sz).exists():
                            os.unlink(self.dst / sj)
                elif s[h]["jsonl"] and not dh.get("zst") and not dh.get("jsonl"):
                    self._link(sj, acts)
            except FileNotFoundError:
                pass  # raced with the writer's compaction; the next pass sees the .zst
            except OSError as e:
                self.err(f"sync {h}: {e.__class__.__name__} {e.errno}")
        acts["prune"] = self.prune()
        self.last_sync = int(self.now())
        return acts

    def _link(self, name: str, acts: dict) -> None:
        acts["link"] += 1
        if not self.dry:
            os.link(self.src / name, self.dst / name)

    def prune(self) -> int:
        cutoff = hour_name(int(self.now()) - self.retain_h * 3600)
        n = 0
        for h in scan(self.dst):
            if h < cutoff:
                n += 1
                if not self.dry:
                    for nm in (f"tape-{h}.jsonl.zst", f"tape-{h}.jsonl"):
                        try:
                            os.unlink(self.dst / nm)
                        except FileNotFoundError:
                            pass
        return n

    # ---- backfill ----
    def _remote(self, cmd: str, **kw):
        return subprocess.run([*self.ssh, self.remote_host, cmd], **kw)

    def plan_backfill(self) -> list:
        s, d = scan(self.src), scan(self.dst)
        first = min(s) if s else hour_name(int(self.now()))
        t = hour_ts(hour_name(int(self.now()) - self.backfill_h * 3600))
        out = []
        while hour_name(t) < first:
            if hour_name(t) not in d:
                out.append(hour_name(t))
            t += 3600
        return out

    def backfill(self, tick=None) -> int:
        todo = self.plan_backfill()
        if self.dry:
            print(f"DRY: backfill plan {len(todo)} hours: {todo[:2]} .. {todo[-2:]}")
            return 0
        if not todo:
            return 0
        r = self._remote(f"ls -1 {shlex.quote(self.remote_dir)}", capture_output=True, text=True)
        if r.returncode != 0:
            self.err(f"remote ls rc={r.returncode}")
            return 0
        have = {h for h, v in scan_names(r.stdout.split()).items() if v["zst"]}
        done = 0
        for h in todo:
            if h not in have:
                continue
            if self.fetch_hour(h):
                done += 1
                self.backfilled += 1
            if tick:
                tick()
        return done

    def fetch_hour(self, h: str) -> bool:
        name = f"tape-{h}.jsonl.zst"
        if free_pct(self.free_path) < self.min_free_pct:
            self.err(f"disk_reserve: free under {self.min_free_pct}%; not writing {h}")
            return False
        remote = f"{self.remote_dir}/{name}"
        part = self.dst / (name + ".part")
        try:
            r = self._remote(f"sha256sum {shlex.quote(remote)}", capture_output=True, text=True)
            want = r.stdout.split()[0] if r.returncode == 0 and r.stdout.split() else ""
            if len(want) != 64:
                self.err(f"{h}: remote sha256sum failed")
                return False
            sha = hashlib.sha256()
            with open(part, "wb") as f:
                p = subprocess.Popen([*self.ssh, self.remote_host, f"cat {shlex.quote(remote)}"], stdout=subprocess.PIPE)
                while True:
                    b = p.stdout.read(CHUNK)
                    if not b:
                        break
                    sha.update(b)
                    f.write(b)
                rc = p.wait()
                f.flush()
                os.fsync(f.fileno())
            if rc != 0 or sha.hexdigest() != want:
                self.err(f"{h}: sha/transfer mismatch rc={rc}")
                os.unlink(part)
                return False
            if (self.dst / name).exists():
                os.unlink(part)
                return False
            os.rename(part, self.dst / name)
            return True
        except OSError as e:
            self.err(f"{h}: {e.__class__.__name__}")
            try:
                os.unlink(part)
            except OSError:
                pass
            return False

    # ---- status ----
    def status(self) -> dict:
        hs = sorted(scan(self.dst))
        return {"v": 1, "hours": len(hs), "earliest": hs[0] if hs else None, "latest": hs[-1] if hs else None, "backfilled": self.backfilled,
                "last_sync": self.last_sync, "errors": self.errors}

    def write_status(self) -> None:
        if self.dry:
            return
        self.meta.mkdir(parents=True, exist_ok=True)
        st = self.status()
        tmp = self.meta / "status.json.tmp"
        tmp.write_text(json.dumps(st))
        os.replace(tmp, self.meta / "status.json")
        if self.progress:
            note = f"hours={st['hours']} earliest={st['earliest']} latest={st['latest']} backfilled={st['backfilled']} errors={len(st['errors'])}"
            try:
                Path(self.progress).write_text(json.dumps({"pct": min(100, int(100 * st["hours"] / max(1, self.backfill_h))), "note": note}))
            except OSError:
                pass


def main(argv=None) -> int:
    e = os.environ.get
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=e("SRC", os.path.expanduser("~/mal-data/arena-tipfeed/tape")))
    ap.add_argument("--dst", default=e("DST", os.path.expanduser("~/mal-data/floor-tip/tape")))
    ap.add_argument("--retain-h", type=int, default=int(e("RETAIN_H", "240")))
    ap.add_argument("--backfill-h", type=int, default=int(e("BACKFILL_H", "230")))
    ap.add_argument("--min-free-pct", type=int, default=int(e("MIN_FREE_PCT", "20")))
    ap.add_argument("--remote-host", default=e("REMOTE_HOST", "mal-research-0"))
    ap.add_argument("--remote-dir", default=e("REMOTE_DIR", DEFAULT_REMOTE))
    ap.add_argument("--interval", type=float, default=float(e("INTERVAL_S", "2")))
    ap.add_argument("--dry", action="store_true", default=e("DRY") == "1")
    ap.add_argument("--once", action="store_true", help="one sync pass (+ backfill) then exit")
    a = ap.parse_args(argv)
    rs, rd = os.path.realpath(a.src), os.path.realpath(a.dst)
    if rs == rd or rd.startswith(rs + os.sep):
        print("REFUSED: dst must be a different dir, not inside src", file=sys.stderr)
        return 2
    if a.backfill_h > a.retain_h:
        print("REFUSED: backfill-h must be <= retain-h", file=sys.stderr)
        return 2
    lk = Linker(a.src, a.dst, retain_h=a.retain_h, backfill_h=a.backfill_h, min_free_pct=a.min_free_pct, remote_host=a.remote_host,
                remote_dir=a.remote_dir, progress=e("MISCUSI_PROGRESS"), dry=a.dry)
    if a.dry:
        print(f"DRY: sync plan {lk.sync_once()}")
        lk.backfill()
        return 0
    stop: list = []
    for sg in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sg, lambda *_: stop.append(1))
    lk.sync_once()
    lk.write_status()

    def tick() -> None:
        lk.sync_once()
        lk.write_status()
        if stop:
            raise KeyboardInterrupt

    try:
        lk.backfill(tick)
        lk.write_status()
        while not stop and not a.once:
            time.sleep(a.interval)
            lk.sync_once()
            lk.write_status()
    except KeyboardInterrupt:
        pass
    lk.write_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
