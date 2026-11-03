"""XFeed directory source (Helm /data/mal/x-feed): staleness, matching, summary, old formats, lookup requests."""
import datetime as dt
import json
import os

from tools.slowbot import fixtures as fx, runner as rn
from tools.slowbot.tests.test_runner import FakeCaller, mk

T0 = 1_800_000_000.0


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def helm(mint=None, ticker=None, age=60, snippets=("a", "b"), **kw):
    r = {"ts_utc": iso(T0 - age), "mint": mint, "ticker": ticker, "mentions_15m": 1, "mentions_60m": 3, "unique_authors_60m": 2,
         "top_author_followers": 8285, "kol_mentions": [], "sentiment": "pos", "snippets": list(snippets), "flags": ["shill", "signal-bot"]}
    r.update(kw)
    return r


def put(d, name, rs):
    (d / name).write_text("\n".join(json.dumps(r) for r in rs) + "\n")


def feed(d):
    return rn.XFeed(str(d), clock=lambda: T0)


def test_dir_newest_feed_file_and_summary(tmp_path):
    put(tmp_path, "x-feed-2026-11-03T0000Z.jsonl", [helm("M1", "$AA", snippets=["old"])])
    put(tmp_path, "x-feed-2026-11-03T0100Z.jsonl", [helm("M1", "$AA", snippets=["s1", "s2", "s3", "s4", "s5", "s6"])])
    out = feed(tmp_path).snippets("M1", "AA")
    assert out[0] == ("X 60m: mentions=3 authors=2 top_followers=8285 kol=0 sentiment=pos flags=shill,signal-bot "
                      "(counts are lower bounds; shill/signal-bot = paid promo, treat as noise or negative)")
    assert out[1:] == ["s1", "s2", "s3", "s4"] and len(out) == 5


def test_dir_latest_jsonl_fallback_and_empty_dir(tmp_path):
    assert feed(tmp_path).snippets("M1", "AA") is None
    put(tmp_path, "latest.jsonl", [helm("M1", "$AA")])
    assert feed(tmp_path).snippets("M1", "AA")[1:] == ["a", "b"]


def test_staleness_feed_30m_lookup_120m(tmp_path):
    put(tmp_path, "x-feed-2026-11-03T0100Z.jsonl", [helm("M1", age=31 * 60), helm("M2", age=29 * 60)])
    put(tmp_path, "x-lookup-2026-11-03T0100Z.jsonl", [helm("M3", age=119 * 60), helm("M4", age=121 * 60)])
    f = feed(tmp_path)
    assert f.snippets("M1", None) is None
    assert f.snippets("M2", None) is not None
    assert f.snippets("M3", None) is not None
    assert f.snippets("M4", None) is None
    put(tmp_path, "x-feed-2026-11-03T0200Z.jsonl", [dict(helm("M5"), ts_utc="garbage")])
    assert feed(tmp_path).snippets("M5", None) is None  # unreadable time on a Helm row = not fresh


def test_mint_match_wins_and_ticker_only_when_row_mint_null(tmp_path):
    put(tmp_path, "x-feed-2026-11-03T0100Z.jsonl", [
        helm(None, "$SPH", snippets=["tick-only"]),
        helm("MINT_A", "$SPH", snippets=["mint-row"]),
        helm("MINT_B", "$OTHER", snippets=["b-row"])])
    f = feed(tmp_path)
    assert f.snippets("MINT_A", "SPH")[1:] == ["mint-row"]  # mint match preferred over the null-mint ticker row
    assert f.snippets("ZZZ", "sph")[1:] == ["tick-only"]  # case-insensitive; the row has $, the candidate does not
    assert f.snippets("ZZZ", "$SpH")[1:] == ["tick-only"]  # candidate $ stripped too
    assert f.snippets("ZZZ", "other") is None  # that row has a mint: no ticker match
    assert f.snippets("ZZZ", None) is None


def test_lookup_rows_are_used(tmp_path):
    put(tmp_path, "x-feed-2026-11-03T0100Z.jsonl", [helm("M9", snippets=["f"])])
    put(tmp_path, "x-lookup-2026-11-03T0050Z.jsonl", [helm("M1", "$L", age=3600, snippets=["looked-up"])])
    assert feed(tmp_path).snippets("M1", "L")[1:] == ["looked-up"]


def test_old_formats_still_work(tmp_path):
    p = tmp_path / "old.jsonl"
    p.write_text(json.dumps("plain") + "\n" + json.dumps({"mint": "M", "text": "txt"}) + "\n")
    f = rn.XFeed(str(p))
    assert f.snippets("M", "z") == ["txt"]
    d = tmp_path / "d"
    d.mkdir()
    put(d, "x-feed-2026-11-03T0100Z.jsonl", [{"mint": "M", "text": "dir-txt"}])  # old-style dict row, no ts_utc, in a directory
    assert feed(d).snippets("M", None) == ["dir-txt"]
    assert f.request_lookup("M") is False  # a file source never writes requests


def test_lookup_dedupe_hourly_cap_and_file_check(tmp_path):
    t = [T0]
    f = rn.XFeed(str(tmp_path), clock=lambda: t[0], lookups=True)
    req = tmp_path / "lookup-requests.txt"
    req.write_text("# c\nPRE")  # no trailing newline
    assert f.request_lookup("PRE") is False  # already in the file
    assert f.request_lookup("M0") is True
    assert f.request_lookup("M0") is False  # local set
    assert req.read_text().splitlines() == ["# c", "PRE", "M0"]
    for i in range(1, 6):
        assert f.request_lookup(f"M{i}") is True  # M0..M5 = 6 appends
    assert f.request_lookup("M6") is False  # cap: 6 an hour
    t[0] += 3601
    assert f.request_lookup("M6") is True
    assert f.request_lookup(None) is False and f.request_lookup("") is False
    lines = req.read_text().splitlines()
    assert len(lines) == len(set(lines))
    f2 = rn.XFeed(str(tmp_path), clock=lambda: t[0], lookups=True)  # a restart: the file still dedupes
    assert f2.request_lookup("M6") is False


def test_lookup_write_failure_is_tolerated(tmp_path):
    f = rn.XFeed(str(tmp_path), clock=lambda: T0, lookups=True)
    os.mkdir(tmp_path / "lookup-requests.txt")  # opening it for read/append fails
    assert f.request_lookup("M1") is False
    gone = rn.XFeed(str(tmp_path / "gone"), clock=lambda: T0, lookups=True)
    assert gone.request_lookup("M1") is False


def test_runner_counts_lookup_requested(tmp_path):
    d = tmp_path / "xf"
    d.mkdir()
    r, _ = mk(tmp_path / "o", caller=FakeCaller(), x_feed=rn.XFeed(str(d), lookups=True))
    r.run(duration=40)
    assert (d / "lookup-requests.txt").read_text().split() == [fx.M["GOOD"]]
    assert r.rs["counters"]["x_lookup_requested"] == 1


def test_lookups_off_by_default_writes_nothing(tmp_path):
    f = rn.XFeed(str(tmp_path), clock=lambda: T0)
    assert f.request_lookup("M1") is False
    assert not (tmp_path / "lookup-requests.txt").exists()
    d = tmp_path / "xf"
    d.mkdir()
    r, _ = mk(tmp_path / "o", caller=FakeCaller(), x_feed=rn.XFeed(str(d)))
    r.run(duration=40)
    assert not (d / "lookup-requests.txt").exists()
    assert r.rs["counters"].get("x_lookup_requested", 0) == 0


def test_hourly_cap_survives_restart(tmp_path):
    t = [T0]
    sp = str(tmp_path / "times.json")
    mk_f = lambda: rn.XFeed(str(tmp_path), clock=lambda: t[0], lookups=True, state_path=sp)
    f = mk_f()
    for i in range(4):
        assert f.request_lookup(f"A{i}") is True
    f2 = mk_f()  # restart
    assert f2.request_lookup("B0") is True and f2.request_lookup("B1") is True
    assert f2.request_lookup("B2") is False
    f3 = mk_f()  # restart again, still inside the hour
    assert f3.request_lookup("B3") is False
    assert len((tmp_path / "lookup-requests.txt").read_text().split()) == 6
    t[0] += 3601
    assert mk_f().request_lookup("B3") is True


def test_cli_flag_and_script_wiring():
    import subprocess
    sh = "scripts/ops/slowbot-paper.sh"
    env = {"PATH": os.environ["PATH"], "DRY": "1", "NO_HAIKU": "1", "X_FEED": "/tmp"}
    on = subprocess.run(["bash", sh], env=dict(env, X_LOOKUPS="1"), capture_output=True, text=True).stdout
    off = subprocess.run(["bash", sh], env=env, capture_output=True, text=True).stdout
    assert "--x-lookups" in on and "--x-lookups" not in off
