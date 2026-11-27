#!/usr/bin/env python3
"""H capture for the open-sim board, per QP-RULING-1127-ce1-successor-H-capture R1-R9.

Run as: python3 -I -B scripts/ops/osb_h_capture.py ...  (stdlib only).

Polls the board build, and for each pair captures the FIRST qualifying build
observed (byte copies of board.json and board.md).  Only the R7 fields are
ever taken from the json; nothing else from the board is printed, logged or
stored.  Checks run on private copies, so the captured bytes are the bytes
tested.  A pair with a CAPTURE or NOT_DECIDABLE file is final (R4).
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

TOP_FIELDS = ("generated_utc", "board_version", "tape_through_utc")
BOOK_FIELDS = ("id", "state", "today_day", "last_hour", "last_hour_end_utc",
               "wall_time_utc")
MIN_TODAY_DAY = "2026-12-01"
DIFFERS_LINE = ("BOARD_VERSION_DIFFERS: rows not to be read until QP rules "
                "(QP-RULING-1127 R6)")


def pr(*args):
    print(*args)
    sys.stdout.flush()


def parse_utc(s):
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d


def now_iso(now_override):
    if now_override:
        return now_override
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def r7_fields(doc):
    """The only function that reads board json content. Returns R7 fields only."""
    if not isinstance(doc, dict):
        return {k: None for k in TOP_FIELDS}, []
    top = {k: doc.get(k) for k in TOP_FIELDS}
    books = []
    raw = doc.get("books")
    if isinstance(raw, list):
        for b in raw:
            if isinstance(b, dict):
                books.append({k: b.get(k) for k in BOOK_FIELDS})
    return top, books


def sha256_file(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def atomic_write(path, data, mode=0o444):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".w-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def pair_name(pair):
    return pair[0] + "_" + pair[1]


def parse_pairs(s):
    out = []
    for item in s.split(","):
        a, sep, b = item.partition(":")
        if not sep or not a or not b:
            raise ValueError("bad pair")
        out.append((a, b))
    if not out:
        raise ValueError("no pairs")
    return out


def md_generated(md_path):
    """Line 5 only: '- Generated <generated_utc>;'."""
    with open(md_path, "rb") as f:
        lines = f.read().split(b"\n")
    if len(lines) < 5:
        return None
    line = lines[4].decode("utf-8", "replace").strip()
    pre = "- Generated "
    if not line.startswith(pre):
        return None
    rest = line[len(pre):]
    return rest.split(";", 1)[0].strip() if ";" in rest else None


def good(x):
    return isinstance(x, str)


def qualifies(by_id, pair, gen_dt, t_from, t_until):
    if not (t_from <= gen_dt <= t_until):
        return False
    for bid in pair:
        b = by_id.get(bid)
        if b is None:
            return False
        if not good(b.get("state")) or b.get("state") != "running":
            return False
        td = b.get("today_day")
        if not good(td) or td < MIN_TODAY_DAY:
            return False
    return True


def rmtree_force(path):
    for root, dirs, files in os.walk(path):
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def prepare_out_dir(out_dir):
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    os.chmod(out_dir, 0o700)
    if shutil.which("setfacl"):
        r = subprocess.run(["setfacl", "-b", "-k", out_dir],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            raise OSError("setfacl failed")
    for p in glob.glob(os.path.join(out_dir, ".poll-*")):
        rmtree_force(p)
    for p in glob.glob(os.path.join(out_dir, "*.tmp")):
        try:
            os.chmod(p, 0o600)
            os.unlink(p)
        except OSError:
            pass


def read_log(log_path):
    recs = []
    if os.path.exists(log_path):
        with open(log_path, "r", errors="replace") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    recs.append(r)
    return recs


def last_generated(recs):
    for r in reversed(recs):
        if isinstance(r.get("generated_utc"), str):
            return r
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--board-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--from", dest="t_from", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--expect-board-version", required=True)
    ap.add_argument("--poll-s", type=float, default=10.0)
    ap.add_argument("--grace-s", type=float, default=900.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--now-utc", default=None)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    try:
        t_from = parse_utc(a.t_from)
        t_until = parse_utc(a.until)
        pairs = parse_pairs(a.pairs)
        if not a.expect_board_version or a.poll_s <= 0 or a.grace_s < 0:
            raise ValueError("bad value")
    except ValueError:
        print("bad arguments", file=sys.stderr)
        return 2
    t_end = t_until + datetime.timedelta(seconds=a.grace_s)
    bj = os.path.join(a.board_dir, "board.json")
    bm = os.path.join(a.board_dir, "board.md")
    if not (os.path.isdir(a.board_dir) and os.access(bj, os.R_OK)
            and os.access(bm, os.R_OK)):
        print("unreadable board-dir", file=sys.stderr)
        return 2
    os.umask(0o077)
    try:
        prepare_out_dir(a.out_dir)
    except OSError:
        print("out-dir not usable", file=sys.stderr)
        return 2
    log_path = os.path.join(a.out_dir, "state-log.jsonl")

    final = set()
    for p in pairs:
        for kind in ("CAPTURE", "NOT_DECIDABLE"):
            if os.path.exists(os.path.join(a.out_dir, "%s-%s.txt" % (kind, pair_name(p)))):
                final.add(p)
    pair_books = {b for p in pairs for b in p}
    recs = read_log(log_path)
    lg = last_generated(recs)
    st = {"gen": lg.get("generated_utc") if lg else None}
    with open(log_path, "a") as f:
        f.write(json.dumps({"event": "job_start", "poll_s": a.poll_s,
                            "job_start_utc": now_iso(a.now_utc)}) + "\n")

    while len(final) < len(pairs) and parse_utc(now_iso(a.now_utc)) <= t_end:
        poll_utc = now_iso(a.now_utc)
        try:
            poll_once(a, pairs, pair_books, final, t_from, t_until, bj, bm,
                      log_path, poll_utc, st)
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as e:
            pr("poll %s error %s" % (poll_utc, type(e).__name__))
        if len(final) == len(pairs) or a.once:
            break
        if parse_utc(now_iso(a.now_utc)) > t_end:
            break
        time.sleep(a.poll_s)

    if parse_utc(now_iso(a.now_utc)) > t_end:
        lg = last_generated(read_log(log_path)) or {}
        by_id = {b.get("id"): b for b in lg.get("books", []) if isinstance(b, dict)}
        for p in pairs:
            if p in final:
                continue
            out = ["NOT_DECIDABLE " + pair_name(p),
                   "reason: no qualifying build by %s (QP-RULING-1127 R4)" % a.until,
                   "grace_s: %g (builds with generated_utc > until are not accepted)"
                   % a.grace_s,
                   "written_utc: " + now_iso(a.now_utc),
                   "last_logged_generated_utc: %s" % lg.get("generated_utc")]
            for bid in p:
                b = by_id.get(bid)
                if b is None:
                    out.append("book %s: not in last logged build" % bid)
                else:
                    out.append("book " + " ".join(
                        "%s=%s" % (k, b.get(k)) for k in BOOK_FIELDS))
            path = os.path.join(a.out_dir, "NOT_DECIDABLE-%s.txt" % pair_name(p))
            atomic_write(path, ("\n".join(out) + "\n").encode(), 0o444)
            final.add(p)
            pr("NOT_DECIDABLE %s sha256=%s" % (pair_name(p), sha256_file(path)))
    return 0


def cap_sha_lines(out_dir, gen):
    """sha256 values recorded in existing CAPTURE files for this build."""
    res = {}
    for p in glob.glob(os.path.join(out_dir, "CAPTURE-*.txt")):
        try:
            txt = open(p).read().splitlines()
        except OSError:
            continue
        if ("generated_utc: " + gen) not in txt:
            continue
        for ln in txt:
            for key in ("board_json:", "board_md:"):
                if ln.startswith(key):
                    res[key] = ln.rsplit("sha256=", 1)[-1]
    return res


def poll_once(a, pairs, pair_books, final, t_from, t_until, bj, bm,
              log_path, poll_utc, st):
    tmpdir = tempfile.mkdtemp(prefix=".poll-", dir=a.out_dir)
    os.chmod(tmpdir, 0o700)
    try:
        cj = os.path.join(tmpdir, "board.json")
        cm = os.path.join(tmpdir, "board.md")
        try:
            shutil.copyfile(bj, cj)
            shutil.copyfile(bm, cm)
            with open(cj, "rb") as f:
                doc = json.loads(f.read().decode("utf-8"))
            top, books = r7_fields(doc)
            md_gen = md_generated(cm)
        except (OSError, ValueError, AttributeError, TypeError) as e:
            pr("poll %s read_error %s" % (poll_utc, type(e).__name__))
            return
        gen = top.get("generated_utc")
        if not isinstance(gen, str):
            pr("poll %s read_error no_generated_utc" % poll_utc)
            return
        if md_gen != gen:
            pr("poll %s generated_utc=%s md_json_mismatch" % (poll_utc, gen))
            return
        try:
            gen_dt = parse_utc(gen)
        except ValueError:
            pr("poll %s read_error bad_generated_utc" % poll_utc)
            return
        by_id = {}
        for b in books:
            if isinstance(b.get("id"), str):
                by_id[b["id"]] = b
        pr("poll %s generated_utc=%s %s" % (poll_utc, gen, " ".join(
            "%s:%s/%s" % (bid, by_id[bid].get("state"), by_id[bid].get("today_day"))
            if bid in by_id else "%s:missing" % bid
            for bid in sorted(pair_books))))
        if st["gen"] != gen:
            rec = dict(top)
            rec["poll_utc"] = poll_utc
            rec["books"] = [by_id[b] for b in sorted(pair_books) if b in by_id]
            prev = st["gen"]
            with open(log_path, "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
            st["gen"] = gen
            st["prev"] = prev

        for p in pairs:
            if p in final or not qualifies(by_id, p, gen_dt, t_from, t_until):
                continue
            g = gen.replace(":", "")
            fj = os.path.join(a.out_dir, "board-H-%s.json" % g)
            fm = os.path.join(a.out_dir, "board-H-%s.md" % g)
            if os.path.exists(fj) or os.path.exists(fm):
                ok = (os.path.exists(fj) and os.path.exists(fm)
                      and sha256_file(fj) == sha256_file(cj)
                      and sha256_file(fm) == sha256_file(cm))
                rec_sha = cap_sha_lines(a.out_dir, gen)
                if ok and rec_sha:
                    ok = (rec_sha.get("board_json:") == sha256_file(fj)
                          and rec_sha.get("board_md:") == sha256_file(fm))
                if not ok:
                    pr("reuse_mismatch %s generated_utc=%s not captured"
                       % (pair_name(p), gen))
                    continue
            else:
                shutil.copyfile(cj, fj)
                shutil.copyfile(cm, fm)
                os.chmod(fj, 0o444)
                os.chmod(fm, 0o444)
            sj, sm = sha256_file(fj), sha256_file(fm)
            bv = top.get("board_version")
            lines = ["captured_utc: " + now_iso(a.now_utc),
                     "pair: " + pair_name(p),
                     "generated_utc: " + gen,
                     "capture_rule: first qualifying build observed at a %g s poll;"
                     " a build replaced within one poll is not seen"
                     % a.poll_s,
                     "previous_logged_generated_utc: %s" % st.get("prev"),
                     "board_version: %s" % bv,
                     "board_json: %s sha256=%s" % (os.path.basename(fj), sj),
                     "board_md: %s sha256=%s" % (os.path.basename(fm), sm)]
            for bid in p:
                lines.append("book " + " ".join(
                    "%s=%s" % (k, by_id[bid].get(k)) for k in BOOK_FIELDS))
            if bv != a.expect_board_version:
                lines.append(DIFFERS_LINE)
            cap = os.path.join(a.out_dir, "CAPTURE-%s.txt" % pair_name(p))
            atomic_write(cap, ("\n".join(lines) + "\n").encode(), 0o444)
            final.add(p)
            pr("CAPTURED %s generated_utc=%s" % (pair_name(p), gen))
            pr("  %s sha256=%s" % (os.path.basename(fj), sj))
            pr("  %s sha256=%s" % (os.path.basename(fm), sm))
            pr("  %s sha256=%s" % (os.path.basename(cap), sha256_file(cap)))
            if bv != a.expect_board_version:
                pr("  " + DIFFERS_LINE)
    finally:
        rmtree_force(tmpdir)


if __name__ == "__main__":
    sys.exit(main())
