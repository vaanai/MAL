#!/usr/bin/env python3
"""H capture for the open-sim board, per QP-RULING-1127-ce1-successor-H-capture R1-R9.

Run as: python3 -I -B scripts/ops/osb_h_capture.py ...  (stdlib only).

Polls the board build, and for each pair captures the FIRST qualifying build
(byte copies of board.json and board.md).  Only the R7 fields are ever taken
from the json; nothing else from the board is printed, logged or stored.
Checks run on private copies, so the captured bytes are the bytes tested.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

TOP_FIELDS = ("generated_utc", "board_version", "tape_through_utc")
BOOK_FIELDS = ("id", "state", "today_day", "last_hour", "last_hour_end_utc",
               "wall_time_utc")
MIN_TODAY_DAY = "2026-12-01"
NOT_RUNNING_MSG = "no qualifying build by {until} (QP-RULING-1127 R4)"
DIFFERS_LINE = ("BOARD_VERSION_DIFFERS: rows not to be read until QP rules "
                "(QP-RULING-1127 R6)")


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
    top = {k: doc.get(k) for k in TOP_FIELDS}
    books = []
    for b in doc.get("books") or []:
        if isinstance(b, dict):
            books.append({k: b.get(k) for k in BOOK_FIELDS})
    return top, books


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def atomic_write(path, data, mode=None):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


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


def qualifies(by_id, pair, gen_dt, t_from, t_until):
    if not (t_from <= gen_dt <= t_until):
        return False
    for bid in pair:
        b = by_id.get(bid)
        if b is None:
            return False
        if b.get("state") != "running":
            return False
        td = b.get("today_day")
        if not isinstance(td, str) or td < MIN_TODAY_DAY:
            return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--board-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--from", dest="t_from", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--expect-board-version-prefix", required=True)
    ap.add_argument("--poll-s", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--now-utc", default=None)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    try:
        t_from = parse_utc(a.t_from)
        t_until = parse_utc(a.until)
        pairs = parse_pairs(a.pairs)
        if "=" not in a.expect_board_version_prefix:
            raise ValueError("bad prefix")
        if a.poll_s <= 0:
            raise ValueError("bad poll")
    except ValueError:
        print("bad arguments", file=sys.stderr)
        return 2
    bj = os.path.join(a.board_dir, "board.json")
    bm = os.path.join(a.board_dir, "board.md")
    if not (os.path.isdir(a.board_dir) and os.access(bj, os.R_OK)
            and os.access(bm, os.R_OK)):
        print("unreadable board-dir", file=sys.stderr)
        return 2

    os.umask(0o077)
    os.makedirs(a.out_dir, mode=0o700, exist_ok=True)
    log_path = os.path.join(a.out_dir, "state-log.jsonl")

    captured = set()
    for p in pairs:
        if os.path.exists(os.path.join(a.out_dir, "CAPTURE-%s.txt" % pair_name(p))):
            captured.add(p)
    last_logged_gen = None
    last_line = None
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            for ln in f:
                if ln.strip():
                    last_line = ln
        if last_line:
            try:
                last_logged_gen = json.loads(last_line).get("generated_utc")
            except ValueError:
                pass
    pair_books = {b for p in pairs for b in p}

    while True:
        poll_utc = now_iso(a.now_utc)
        state_holder = {"gen": last_logged_gen}
        poll_once(a, pairs, pair_books, captured, t_from, t_until, bj, bm,
                  log_path, poll_utc, state_holder)
        last_logged_gen = state_holder["gen"]
        done = len(captured) == len(pairs)
        past = parse_utc(now_iso(a.now_utc)) > t_until
        if done or past or a.once:
            break
        time.sleep(a.poll_s)

    if len(captured) < len(pairs) and parse_utc(now_iso(a.now_utc)) > t_until:
        last = {}
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                lines = [x for x in f if x.strip()]
            if lines:
                last = json.loads(lines[-1])
        by_id = {b.get("id"): b for b in last.get("books", [])}
        for p in pairs:
            if p in captured:
                continue
            out = ["NOT_DECIDABLE " + pair_name(p),
                   "reason: " + NOT_RUNNING_MSG.format(until=a.until),
                   "written_utc: " + now_iso(a.now_utc),
                   "last_logged_generated_utc: %s" % last.get("generated_utc")]
            for bid in p:
                b = by_id.get(bid)
                if b is None:
                    out.append("book %s: not in last logged build" % bid)
                else:
                    out.append("book " + " ".join(
                        "%s=%s" % (k, b.get(k)) for k in BOOK_FIELDS))
            path = os.path.join(a.out_dir, "NOT_DECIDABLE-%s.txt" % pair_name(p))
            atomic_write(path, ("\n".join(out) + "\n").encode(), 0o444)
            print("NOT_DECIDABLE %s sha256=%s" % (pair_name(p), sha256_file(path)))
    return 0


def poll_once(a, pairs, pair_books, captured, t_from, t_until, bj, bm,
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
        except (OSError, ValueError, AttributeError) as e:
            print("poll %s read_error %s" % (poll_utc, type(e).__name__))
            return
        gen = top.get("generated_utc")
        if not isinstance(gen, str):
            print("poll %s read_error no_generated_utc" % poll_utc)
            return
        if md_gen != gen:
            print("poll %s generated_utc=%s md_json_mismatch" % (poll_utc, gen))
            return
        try:
            gen_dt = parse_utc(gen)
        except ValueError:
            print("poll %s read_error bad_generated_utc" % poll_utc)
            return
        by_id = {b["id"]: b for b in books}
        print("poll %s generated_utc=%s %s" % (poll_utc, gen, " ".join(
            "%s:%s/%s" % (bid, by_id[bid].get("state"), by_id[bid].get("today_day"))
            if bid in by_id else "%s:missing" % bid
            for bid in sorted(pair_books))))
        if st["gen"] != gen:
            rec = dict(top)
            rec["poll_utc"] = poll_utc
            rec["books"] = [by_id[b] for b in sorted(pair_books) if b in by_id]
            with open(log_path, "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
            st["gen"] = gen

        for p in pairs:
            if p in captured or not qualifies(by_id, p, gen_dt, t_from, t_until):
                continue
            g = gen.replace(":", "")
            fj = os.path.join(a.out_dir, "board-H-%s.json" % g)
            fm = os.path.join(a.out_dir, "board-H-%s.md" % g)
            if not (os.path.exists(fj) and os.path.exists(fm)):
                os.replace(cj, fj)
                os.replace(cm, fm)
                os.chmod(fj, 0o444)
                os.chmod(fm, 0o444)
                # the temp copies are gone; later pairs reuse the saved files
            sj, sm = sha256_file(fj), sha256_file(fm)
            lines = ["captured_utc: " + now_iso(a.now_utc),
                     "pair: " + pair_name(p),
                     "generated_utc: " + gen,
                     "board_version: %s" % top.get("board_version"),
                     "board_json: %s sha256=%s" % (os.path.basename(fj), sj),
                     "board_md: %s sha256=%s" % (os.path.basename(fm), sm)]
            for bid in p:
                lines.append("book " + " ".join(
                    "%s=%s" % (k, by_id[bid].get(k)) for k in BOOK_FIELDS))
            bv = top.get("board_version")
            if not isinstance(bv, str) or a.expect_board_version_prefix not in bv:
                lines.append(DIFFERS_LINE)
            cap = os.path.join(a.out_dir, "CAPTURE-%s.txt" % pair_name(p))
            atomic_write(cap, ("\n".join(lines) + "\n").encode(), 0o444)
            captured.add(p)
            print("CAPTURED %s generated_utc=%s" % (pair_name(p), gen))
            print("  %s sha256=%s" % (os.path.basename(fj), sj))
            print("  %s sha256=%s" % (os.path.basename(fm), sm))
            print("  %s sha256=%s" % (os.path.basename(cap), sha256_file(cap)))
            if bv is None or a.expect_board_version_prefix not in str(bv):
                print("  " + DIFFERS_LINE)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
