"""Tests for tools/feed_handover_proof.py on SYNTHETIC tape only (no arena data, no network). P35 PR 1."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import feed_handover_proof as P  # noqa: E402

H0 = int(datetime(2026, 11, 8, 14, tzinfo=timezone.utc).timestamp() * 1000)
MIN = 60_000
SEAM = H0 + 20 * MIN
W0, W1 = H0 + 10 * MIN, H0 + 30 * MIN
PROGS = ("pumpswap", "pumpfun")
OFF = {"pumpswap": 0, "pumpfun": 500}
SLOT0 = 300_000_000


def name_for(t: int) -> str:
    return "tape-" + datetime.fromtimestamp(t // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def trade(prog: str, t: int, *, shift: int = 0) -> dict:
    """One synthetic trade row; its signature and slot are a function of (prog, nominal t), so two writers' copies share a key."""
    return {"v": 1, "type": "trade", "prog": prog, "slot": SLOT0 + (t - H0) // 400, "t_recv_ms": t + shift, "signature": f"sig-{prog}-{t}",
            "event_index": 0, "venue": "pumpswap" if prog == "pumpswap" else "pump_bonding", "mint": f"mint-{prog}-{(t // 7000) % 13}",
            "side": "buy" if (t // 1000) % 2 else "sell", "sol_lamports": 1_000_000 + (t % 977), "token_raw": 5_000 + (t % 31),
            "quote_reserve": 80_000_000_000, "base_reserve": 900_000_000_000_000}


def hb(prog: str, t: int) -> dict:
    return {"v": 1, "type": "hb", "prog": prog, "slot": 0, "t_recv_ms": t, "signature": None, "event_index": None, "rows_last_min": 60}


def meta(typ: str, t: int, **body) -> dict:
    return {"v": 1, "type": typ, "prog": "all", "slot": 0, "t_recv_ms": t, "signature": None, "event_index": None, **body}


def stream(t_from: int, t_to: int) -> list[dict]:
    """Every data row of both programs with nominal t in [t_from, t_to), one per program per second, in time order."""
    out = []
    for s in range((t_from - H0) // 1000, (t_to - H0) // 1000 + 1):
        for p in PROGS:
            t = H0 + s * 1000 + OFF[p]
            if t_from <= t < t_to:
                out.append(trade(p, t))
    return sorted(out, key=lambda r: r["t_recv_ms"])


def hbs(t_from: int, t_to: int, phase: int = 250) -> list[dict]:
    out = []
    t = H0 + phase
    while t < t_to:
        if t >= t_from:
            out += [hb(p, t) for p in PROGS]
        t += MIN
    return out


def write(d: Path, rows: list[dict]) -> Path:
    """Append rows in the given (append) order, each to the file of its t_recv hour, or of row['_hour_of'] when set (a late row in the current hour)."""
    d.mkdir(parents=True, exist_ok=True)
    for r in rows:
        r = dict(r)
        name = name_for(r.pop("_hour_of", r["t_recv_ms"]))
        with open(d / name, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return d


def by_time(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def continuous(t_from: int = H0, t_to: int = H0 + 60 * MIN) -> list[dict]:
    return by_time(stream(t_from, t_to) + hbs(t_from, t_to))


OLD_LAST = {"pumpswap": SEAM - 2000, "pumpfun": SEAM - 1500}
LOCK = SEAM + 300


MDR = {p: 0.01 for p in PROGS}  # pinned T_p for the tests
MOR = {p: 500 for p in PROGS}  # pinned minimum n_old for the tests


def takeover_rows(*, continuity=None, old_last=None, cover=None, stop=True, dup=False, overlap_min_s=120, dedup=None, agreement=None, recon=None,
                  thresholds=None, drop=()) -> list[dict]:
    """Old writer to SEAM - 1500 ms, its stop row, the takeover row at LOCK, the rows the old writer missed (buffer order), then the new writer live.
    The takeover row's counts are consistent with the tape unless a test overrides them; `drop` pops takeover fields."""
    old = by_time(stream(H0, SEAM - 1499) + hbs(H0, SEAM - 1000))
    if stop:
        old.append(meta("stop", SEAM - 1000, lines_written=len(old)))
    cover = cover if cover is not None else {p: SEAM - 10 * MIN for p in PROGS}
    n_old = {p: sum(1 for r in old if r["type"] == "trade" and r["prog"] == p and cover[p] <= r["t_recv_ms"] <= OLD_LAST[p]) for p in PROGS}
    tk = meta("takeover", LOCK, schema="arena_tape_v1", handover_v=1, code_sha="0" * 40, pid=2, prev_pid=1, standby_started_ms=SEAM - 11 * MIN,
              old_stop_ms=SEAM - 1000, lock_acquired_ms=LOCK, cover_from_ms=cover, old_last_data_ms=old_last if old_last is not None else dict(OLD_LAST),
              continuity=continuity if continuity is not None else {p: "proven" for p in PROGS},
              dedup=dedup if dedup is not None else {"buffered": 1200, "dropped_dup": 1197, "written_missed": 3, "buffer_overflow": False},
              agreement=agreement if agreement is not None else {p: {"n_old": n_old[p], "only_old": 2} for p in PROGS},
              standby_reconnects_in_overlap=recon if recon is not None else {p: 0 for p in PROGS},
              thresholds=thresholds if thresholds is not None else {"max_disagree_rate": dict(MDR), "min_overlap_rows": dict(MOR)},
              counter_base={"pauses": 0, "rows_dropped_total": 0}, overlap_min_s=overlap_min_s)
    for k in drop:
        tk.pop(k, None)
    missed = stream(SEAM - 1499, LOCK + 1)  # nominal t in (old last, lock]: the old writer never wrote them
    if dup:
        missed = [trade("pumpswap", OLD_LAST["pumpswap"])] + missed  # a row the old writer already wrote: dedup failed
    live = stream(LOCK + 1, H0 + 60 * MIN)
    t = LOCK + 5  # the new writer's first hb right after the takeover row, then every 60 s
    while t < H0 + 60 * MIN:
        live += [hb(p, t) for p in PROGS]
        t += MIN
    return old + [tk] + missed + by_time(live)


def seam(d: Path, **kw) -> dict:
    kw.setdefault("max_disagree_rate", dict(MDR))
    kw.setdefault("min_overlap_rows", dict(MOR))
    return P.seam_report(d, W0, W1, **kw)


# ---- seam mode ------------------------------------------------------------------------------------------------
def test_control_window_single_writer_passes(tmp_path):
    out = seam(write(tmp_path / "t", continuous()))
    assert out["verdict"] == "PASS", out["failed"]
    assert out["checks"]["takeover"]["value"] == 0
    assert out["checks"]["duplicates"]["keys_in_window"] == 2 * 20 * 60


def test_takeover_seam_passes(tmp_path):
    out = seam(write(tmp_path / "t", takeover_rows()), expect_takeover=True)
    assert out["verdict"] == "PASS", out["failed"]
    tk = out["checks"]["takeover"]["rows"][0]
    assert tk["old_stop_row"] and all(v["claim_matches_tape"] and v["overlap_ok"] for v in tk["programs"].values())
    assert tk["programs"]["pumpswap"]["overlap_ms"] == OLD_LAST["pumpswap"] - (SEAM - 10 * MIN)
    assert tk["dedup"]["written_missed_tape"] == 3 and tk["programs"]["pumpfun"]["n_old_tape"] == 599 and tk["failed"] == []
    assert out["markers_in_window"] == {"stop": 1, "takeover": 1}
    assert out["checks"]["hb_gap"]["programs"]["pumpswap"]["value"] <= 75_000


def test_expect_takeover_but_none_fails(tmp_path):
    out = seam(write(tmp_path / "t", continuous()), expect_takeover=True)
    assert out["failed"] == ["takeover"]


def test_duplicate_after_takeover_fails(tmp_path):
    out = seam(write(tmp_path / "t", takeover_rows(dup=True)), expect_takeover=True)
    assert "duplicates" in out["failed"]
    assert out["checks"]["duplicates"]["value"] == 1 and out["checks"]["duplicates"]["extra_copies"] == 1


def test_legacy_restart_fails_on_start_and_hard_gap(tmp_path):
    old = by_time(stream(H0, SEAM - 1499) + hbs(H0, SEAM - 1000)) + [meta("stop", SEAM - 1000)]
    new = [meta("start", SEAM + 5000, schema="arena_tape_v1"),
           meta("gap", SEAM + 5001, kind="process_restart", data_loss=True, from_ms=SEAM - 1000, to_ms=SEAM + 5000, count=1, clean_stop=True)]
    new += by_time(stream(SEAM + 5000, H0 + 60 * MIN) + hbs(SEAM + 5000, H0 + 60 * MIN))
    out = seam(write(tmp_path / "t", old + new))
    assert {"start_rows", "hard_gaps"} <= set(out["failed"])
    assert out["checks"]["hard_gaps"]["kinds"] == {"process_restart": 1}


def test_unproven_continuity_fails(tmp_path):
    out = seam(write(tmp_path / "t", takeover_rows(continuity={"pumpswap": "proven", "pumpfun": "unproven"})), expect_takeover=True)
    assert out["failed"] == ["takeover"]
    assert out["checks"]["takeover"]["rows"][0]["programs"]["pumpfun"]["continuity_proven"] is False


def test_claim_not_matching_tape_fails(tmp_path):
    bad = dict(OLD_LAST, pumpswap=OLD_LAST["pumpswap"] + 1000)
    out = seam(write(tmp_path / "t", takeover_rows(old_last=bad)), expect_takeover=True)
    assert out["failed"] == ["takeover"]
    assert out["checks"]["takeover"]["rows"][0]["programs"]["pumpswap"]["claim_matches_tape"] is False


def test_overlap_too_short_fails(tmp_path):
    cover = {p: OLD_LAST[p] - 60_000 for p in PROGS}  # 60 s of overlap, 120 s required
    out = seam(write(tmp_path / "t", takeover_rows(cover=cover)), expect_takeover=True)
    assert out["failed"] == ["takeover"]
    assert out["checks"]["takeover"]["rows"][0]["programs"]["pumpfun"]["overlap_ok"] is False


def test_takeover_without_old_stop_row_fails(tmp_path):
    out = seam(write(tmp_path / "t", takeover_rows(stop=False)), expect_takeover=True)
    assert out["failed"] == ["takeover"]
    assert out["checks"]["takeover"]["rows"][0]["old_stop_row"] is False


def test_silence_across_seam_fails(tmp_path):
    rows = [r for r in continuous() if not (r["type"] == "trade" and SEAM - 15_000 <= r["t_recv_ms"] < SEAM + 15_000)]
    out = seam(write(tmp_path / "t", rows))
    assert "silence" in out["failed"]
    assert out["checks"]["silence"]["programs"]["pumpswap"]["value"] > 20_000


def test_slot_jump_fails_and_can_be_report_only(tmp_path):
    rows = continuous()
    for r in rows:
        if r["type"] == "trade" and r["t_recv_ms"] >= SEAM:
            r["slot"] += 100
    d = write(tmp_path / "t", rows)
    assert seam(d)["failed"] == ["slot_step"]
    out = seam(d, max_slot_step=0)
    assert out["verdict"] == "PASS" and out["checks"]["slot_step"]["programs"]["pumpfun"]["value"] > 100


def test_hb_gap_fails(tmp_path):
    rows = [r for r in continuous() if not (r["type"] == "hb" and SEAM - 2 * MIN < r["t_recv_ms"] < SEAM + 2 * MIN)]
    out = seam(write(tmp_path / "t", rows))
    assert out["failed"] == ["hb_gap"]


def test_bad_line_fails(tmp_path):
    d = write(tmp_path / "t", continuous())
    with open(d / name_for(SEAM), "a") as fh:
        fh.write('{"v":1,"type":"tra\n')
    assert seam(d)["failed"] == ["bad_lines"]


def test_order_violation_fails(tmp_path):
    rows = continuous()
    i = next(i for i, r in enumerate(rows) if r["t_recv_ms"] >= SEAM)
    rows.insert(i, meta("gap", SEAM, kind="silence", data_loss=True, from_ms=SEAM - 30_000, to_ms=SEAM - 25_000, closed=True))
    out = seam(write(tmp_path / "t", rows))
    assert "order" in out["failed"] and "hard_gaps" in out["failed"]


def test_soft_gap_counted_not_failed(tmp_path):
    rows = continuous()
    rows.append(meta("gap", SEAM, kind="socket_reconnect", data_loss=False, from_ms=SEAM, to_ms=SEAM))
    out = seam(write(tmp_path / "t", by_time(rows)))
    assert out["verdict"] == "PASS" and out["checks"]["hard_gaps"]["soft_gaps"] == 1


def test_late_row_in_next_hour_file_is_read(tmp_path):
    rows = continuous(H0, H0 + 60 * MIN)
    late = trade("pumpswap", OLD_LAST["pumpswap"])  # a second copy of an in-window key, appended to the NEXT hour's file
    late["_hour_of"] = H0 + 61 * MIN
    out = seam(write(tmp_path / "t", rows + [late]))
    assert out["checks"]["duplicates"]["value"] == 1


def test_far_hours_are_not_opened(tmp_path):
    d = write(tmp_path / "t", continuous())
    (d / name_for(H0 - 10 * 3_600_000)).write_text("garbage\n")
    (d / name_for(H0 + 10 * 3_600_000)).write_text("garbage\n")
    out = seam(d)
    assert out["verdict"] == "PASS" and out["files_read"] == 1


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI not on PATH")
def test_zst_hour_and_jsonl_preferred(tmp_path):
    d = write(tmp_path / "t", continuous())
    src = d / name_for(SEAM)
    subprocess.run(["zstd", "-q", str(src), "-o", str(src) + ".zst"], check=True)
    src.unlink()
    assert seam(d)["verdict"] == "PASS"  # read through the .zst
    src.write_text("garbage\n" * 3)  # both exist: the .jsonl is the one read
    assert {"bad_lines", "hb_gap", "silence", "slot_step"} <= set(seam(d)["failed"])


def tk_fail(tmp_path, **kw) -> list[str]:
    seam_kw = {k: kw.pop(k) for k in ("max_disagree_rate", "min_overlap_rows", "overlap_min_s_cli") if k in kw}
    if "overlap_min_s_cli" in seam_kw:
        seam_kw["overlap_min_s"] = seam_kw.pop("overlap_min_s_cli")
    out = seam(write(tmp_path / "t", takeover_rows(**kw)), expect_takeover=True, **seam_kw)
    assert out["failed"] == ["takeover"], out["failed"]
    return out["checks"]["takeover"]["rows"][0]["failed"]


def test_buffer_overflow_fails(tmp_path):
    assert tk_fail(tmp_path, dedup={"buffered": 1200, "dropped_dup": 1197, "written_missed": 3, "buffer_overflow": True}) == ["overflow_false"]


def test_overflow_flag_missing_fails(tmp_path):
    assert tk_fail(tmp_path, dedup={"buffered": 1200, "dropped_dup": 1197, "written_missed": 3}) == ["overflow_false"]


def test_dedup_sum_mismatch_fails(tmp_path):
    assert tk_fail(tmp_path, dedup={"buffered": 1201, "dropped_dup": 1197, "written_missed": 3, "buffer_overflow": False}) == ["sum_ok"]


def test_written_missed_not_matching_tape_fails(tmp_path):
    f = tk_fail(tmp_path, dedup={"buffered": 1199, "dropped_dup": 1197, "written_missed": 2, "buffer_overflow": False})
    assert f == ["written_missed_matches_tape"]


def test_n_old_not_matching_tape_fails(tmp_path):
    f = tk_fail(tmp_path, agreement={"pumpswap": {"n_old": 598, "only_old": 2}, "pumpfun": {"n_old": 599, "only_old": 2}})
    assert f == ["n_old_matches_tape"]


def test_disagreement_above_pinned_rate_fails(tmp_path):
    f = tk_fail(tmp_path, agreement={"pumpswap": {"n_old": 599, "only_old": 7}, "pumpfun": {"n_old": 599, "only_old": 2}})
    assert f == ["disagree_ok"]  # 7 / 599 = 1.17% > 1%


def test_twenty_shared_of_many_no_longer_passes(tmp_path):
    # the old rule (>= 20 shared signatures) would pass a standby that saw 20 of 599 rows; the agreement rate refuses it
    f = tk_fail(tmp_path, agreement={p: {"n_old": 599, "only_old": 579} for p in PROGS})
    assert f == ["disagree_ok"]


def test_too_few_overlap_rows_fails(tmp_path):
    f = tk_fail(tmp_path, min_overlap_rows={p: 600 for p in PROGS}, thresholds={"max_disagree_rate": dict(MDR), "min_overlap_rows": {p: 600 for p in PROGS}})
    assert f == ["n_old_ok"]


def test_standby_reconnect_in_overlap_fails(tmp_path):
    assert tk_fail(tmp_path, recon={"pumpswap": 0, "pumpfun": 1}) == ["no_standby_reconnect"]
    assert tk_fail(tmp_path / "b", drop=("standby_reconnects_in_overlap",)) == ["no_standby_reconnect"]


def test_unpinned_thresholds_fail(tmp_path):
    out = P.seam_report(write(tmp_path / "t", takeover_rows()), W0, W1, expect_takeover=True)
    assert out["failed"] == ["takeover"]
    f = out["checks"]["takeover"]["rows"][0]["failed"]
    assert "thresholds_unpinned" in f


def test_row_thresholds_differing_from_pinned_fail(tmp_path):
    f = tk_fail(tmp_path, thresholds={"max_disagree_rate": {p: 0.5 for p in PROGS}, "min_overlap_rows": dict(MOR)})
    assert f == ["thresholds_differ_from_row"]


def test_overlap_floor_cannot_be_lowered_by_row(tmp_path):
    cover = {p: OLD_LAST[p] - 100_000 for p in PROGS}  # 100 s of overlap; the row claims a 60 s minimum, the floor is 120 s
    n_old = {p: 101 for p in PROGS}
    f = tk_fail(tmp_path, cover=cover, overlap_min_s=60, agreement={p: {"n_old": n_old[p], "only_old": 0} for p in PROGS},
                min_overlap_rows={p: 100 for p in PROGS}, thresholds={"max_disagree_rate": dict(MDR), "min_overlap_rows": {p: 100 for p in PROGS}})
    assert f == ["overlap_ok"]


def test_parse_per_prog():
    assert P.parse_per_prog("0.01", float, PROGS) == {"pumpswap": 0.01, "pumpfun": 0.01}
    assert P.parse_per_prog("pumpswap=0.01,pumpfun=0.02", float, PROGS) == {"pumpswap": 0.01, "pumpfun": 0.02}
    assert P.parse_per_prog(None, int, PROGS) is None
    with pytest.raises(ValueError):
        P.parse_per_prog("pumpswap=", float, PROGS)


def test_hard_kinds_cover_engine_hard_gap_kinds():
    """QP-RULING-1026 C1: every kind arena_paper treats as a hard gap is a hard gap here too."""
    try:
        from tools import arena_paper

        engine_kinds = set(arena_paper.HARD_GAP_KINDS)
    except Exception:  # noqa: BLE001 - a bare env without numpy: read the literal from the source
        import ast

        src = (Path(__file__).resolve().parent / "arena_paper.py").read_text()
        node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "HARD_GAP_KINDS" for t in n.targets))
        engine_kinds = set(ast.literal_eval(node.value))
    assert engine_kinds and engine_kinds <= P.HARD_KINDS
    assert "feed_start" in P.HARD_KINDS


def test_feed_start_gap_row_is_hard(tmp_path):
    rows = continuous()
    rows.append(meta("gap", SEAM, kind="feed_start", data_loss=False, from_ms=SEAM - 1000, to_ms=SEAM))
    out = seam(write(tmp_path / "t", by_time(rows)))
    assert "hard_gaps" in out["failed"] and out["checks"]["hard_gaps"]["kinds"] == {"feed_start": 1}


# ---- parity mode ----------------------------------------------------------------------------------------------
def reference(shift: int = 3) -> list[dict]:
    rows = continuous()
    for r in rows:
        r["t_recv_ms"] += shift
    return rows


def test_parity_takeover_tape_equals_single_writer(tmp_path):
    a = write(tmp_path / "a", takeover_rows())
    b = write(tmp_path / "b", reference())
    out = P.parity_report(a, b, W0, W1, seam_ms=SEAM)
    assert out["verdict"] == "PARITY_EXACT", out
    assert out["totals"]["both"] == out["totals"]["n_a"] == out["totals"]["n_b"] == 2 * 20 * 60
    assert out["seam_miss_rate_not_above_control"] is True


def test_parity_counts_missing_and_mismatched_fields(tmp_path):
    rows = takeover_rows()
    rows = [r for r in rows if not (r["type"] == "trade" and r["t_recv_ms"] == SEAM)]  # A lost one seam row
    for r in rows:
        if r["type"] == "trade" and r["t_recv_ms"] == W0 + 5 * MIN:
            r["mint"] = None  # a cold pool-mint cache
    a = write(tmp_path / "a", rows)
    b = write(tmp_path / "b", reference())
    out = P.parity_report(a, b, W0, W1, seam_ms=SEAM, seam_pad_s=60)
    assert out["verdict"] == "PARITY_DIFF"
    assert out["totals"]["only_b"] == 1 and out["totals"]["only_a"] == 0 and out["totals"]["mismatched_rows"] == 1
    assert out["mismatched_field_names"] == {"mint": 1}
    assert out["buckets"]["seam"]["only_b"] == 1 and out["buckets"]["control"]["only_b"] == 0
    assert out["seam_miss_rate_not_above_control"] is False


def test_parity_edge_straddle_not_missing(tmp_path):
    # B received every row 2 s later: a row at W1 - 1 s in A is at W1 + 1 s in B. Neither copy is "missing".
    a = write(tmp_path / "a", continuous())
    b = write(tmp_path / "b", reference(shift=2000))
    out = P.parity_report(a, b, W0, W1)
    assert out["totals"]["only_a"] == 0 and out["totals"]["only_b"] == 0 and out["verdict"] == "PARITY_EXACT"


# ---- cli, guards, honesty -------------------------------------------------------------------------------------
def run_cli(*args) -> tuple[int, str, str]:
    p = subprocess.run([sys.executable, "-m", "tools.feed_handover_proof", *args], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    return p.returncode, p.stdout, p.stderr


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_cli_pass_fail_and_counts_only(tmp_path):
    d = write(tmp_path / "t", takeover_rows())
    rc, out, _ = run_cli("seam", "--tape", str(d), "--from", iso(W0), "--to", str(W1), "--expect-takeover", "--max-disagree-rate", "0.01",
                         "--min-overlap-rows", "pumpswap=500,pumpfun=500")
    assert rc == 0 and json.loads(out)["verdict"] == "PASS"
    rc, out, _ = run_cli("seam", "--tape", str(d), "--from", iso(W0), "--to", str(W1), "--expect-takeover")  # unpinned thresholds
    assert rc == 1 and "thresholds_unpinned" in out
    assert "sig-" not in out and "mint-" not in out  # never a signature or mint in the output
    d2 = write(tmp_path / "t2", takeover_rows(dup=True))
    rc, out, _ = run_cli("seam", "--tape", str(d2), "--from", iso(W0), "--to", iso(W1))
    assert rc == 1 and "sig-" not in out
    b = write(tmp_path / "b", reference())
    rc, out, _ = run_cli("parity", "--tape-a", str(d), "--tape-b", str(b), "--from", iso(W0), "--to", iso(W1), "--seam-ms", iso(SEAM))
    assert rc == 0 and "sig-" not in out and "mint-" not in out


@pytest.mark.parametrize("path", ["/data/mal/meta-capture/creates", "/tmp/x/ledger/y", "/tmp/decisions", "/tmp/bots.json"])
def test_forbidden_paths_refused(path):
    rc, _, err = run_cli("seam", "--tape", path, "--from", iso(W0), "--to", iso(W1))
    assert rc == 2 and "refused" in err


def test_production_tape_needs_ruling(tmp_path):
    with pytest.raises(P.Refused):
        P.check_path("/data/mal/arena/tape", None)
    with pytest.raises(P.Refused):
        P.check_path("/data/mal/arena/tape", str(tmp_path / "missing.md"))


def test_bad_window_refused(tmp_path):
    d = write(tmp_path / "t", continuous())
    rc, _, err = run_cli("seam", "--tape", str(d), "--from", iso(W1), "--to", iso(W0))
    assert rc == 2
    rc, _, _ = run_cli("seam", "--tape", str(d), "--from", "2026-11-08T14:00:00", "--to", iso(W1))
    assert rc == 2


def test_constants_match_feed_rules():
    try:
        from tools import h5_shadow
    except Exception as exc:  # pragma: no cover - heavy import chain absent in a bare env
        pytest.skip(f"tools.h5_shadow not importable: {type(exc).__name__}")
    assert P.GAP_SLOTS == h5_shadow.GAP_SLOTS and P.SILENCE_S == h5_shadow.SILENCE_S


def test_arena_feed_v1_untouched():
    """HV4's feed pin is the blob of tools/arena_feed.py at f5bd26e7 (sha256 7a9d424b...). It is read from git, not from the working tree: the file on
    main may change (claude/arena-feed-late-dedupe), the pinned blob may not."""
    import hashlib
    import subprocess

    repo = Path(__file__).resolve().parent.parent
    try:
        blob = subprocess.run(["git", "cat-file", "blob", "f5bd26e7:tools/arena_feed.py"], cwd=repo, capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("commit f5bd26e7 is not in this clone")
    assert hashlib.sha256(blob).hexdigest().startswith("7a9d424b")


def test_slim_read_keeps_markers_whole_and_drops_trade_bodies(tmp_path):
    d = write(tmp_path / "t", takeover_rows())
    rd = P.read_rows(d, W0, W1, (W0, W1), slim=True)
    tr = next(r for _, r in rd["rows"] if r["type"] == "trade")
    tk = next(r for _, r in rd["rows"] if r["type"] == "takeover")
    assert set(tr) == set(P.SLIM_KEYS) and "mint" not in tr
    assert "agreement" in tk and "dedup" in tk
