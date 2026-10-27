"""HV7 request-dump tests (tools/hv7_dump_requests.py). SYNTHETIC tape in a temp root, StubModel only: no API call, no key, no network, no /data/mal path.
Nothing here says HV7 makes money."""
from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from tools import hv7_dump_requests as DR
from tools import hv7_rank as RK
from tools.test_hv4_runner import write_tape
from tools.test_hv7_runner import HOUR, MIN, T0, T_D1, T_D2, T_E2, _env, leader_first, make, tape_rows  # noqa: F401 - _env is an autouse fixture

DAY = "2026-09-25"
START = T0 + HOUR
END = T_E2 + 5 * MIN


@pytest.fixture(autouse=True)
def _no_net(monkeypatch, tmp_path):
    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(DR, "OUT_ROOT", str(tmp_path / "rehearsal"))


class Recorder:
    def __init__(self, inner):
        self.inner, self.seen = inner, []

    def complete(self, request, now_ms):
        self.seen.append((now_ms, request))
        return self.inner.complete(request, now_ms)


def test_the_dumped_request_equals_what_the_runner_sends(tmp_path):
    run = make(tmp_path)
    rec = run.model = Recorder(run.model)
    run.run(START, END)
    assert len(rec.seen) >= 2
    sets = dict(DR.collect_sets([DAY], tmp_path / "scratch", tmp_path / "tape", start_ms=START, end_ms=END))
    assert set(sets) == {T for T, _ in rec.seen}
    for T, req in rec.seen:
        (tag, rr), = DR.requests_for_set(T, sets[T], tags=(None,))           # tag None = the runner's L request
        q = rr.request
        assert (q.system, q.user_text, q.ids, q.card_shas, q.nonce, q.seq, q.max_tokens) == (req.system, req.user_text, req.ids, req.card_shas, req.nonce, req.seq, req.max_tokens)
        assert q.request_sha256() == req.request_sha256()
        for tg, ab in DR.requests_for_set(T, sets[T]):                        # A and B: same cards, same builder, same seq and template, only the display order differs
            assert tg in ("A", "B") and sorted(ab.shown_ids) == sorted(req.ids) and ab.screen_card_shas == rr.screen_card_shas
            assert ab.request.seq == req.seq and ab.request.template_sha256 == req.template_sha256 and ab.request.playbook_sha256 == req.playbook_sha256


def test_files_have_six_calls_and_the_manifest_matches(tmp_path):
    out = tmp_path / "rehearsal" / "o1"
    write_tape(tmp_path / "tape", tape_rows())
    res = DR.dump([DAY], out, tmp_path / "tape", tmp_path / "scratch")
    man = json.loads((out / "manifest.json").read_text())
    assert res["calls"] == man["calls"] == 2 * man["sets"] >= 6 and res["api_calls"] == 0
    files = sorted((out / "prompts").glob("batch-*.jsonl"))
    assert [f["file"] for f in man["files"]] == [f"prompts/{f.name}" for f in files] and files[0].name == "batch-00.jsonl"
    total = 0
    for f, m in zip(files, man["files"]):
        data = f.read_bytes()
        assert hashlib.sha256(data).hexdigest() == m["sha256"]
        lines = [json.loads(x) for x in data.splitlines()]
        total += len(lines)
        assert len(lines) == m["calls"] and (len(lines) == DR.CALLS_PER_FILE or f == files[-1])
        for r in lines:
            assert set(r) == {"chunk_id", "set_id", "shuffle", "card_ids", "system", "user"} and r["chunk_id"] == f.stem and r["shuffle"] in ("A", "B")
            assert 3 <= len(r["card_ids"]) <= RK.N_MAX and r["set_id"] % HOUR == 2 * MIN
    assert total == man["calls"] and len(man["request_sha256"]) == man["calls"]
    assert sum(man["n_histogram"].values()) == man["sets"] and sum(man["sets_per_day"].values()) == man["sets"]
    assert "price" not in json.dumps(man) and man["outcome_free"] is True


def test_as_of_safety_nothing_after_t_d_reaches_a_request(tmp_path):
    full = tmp_path / "full"
    write_tape(full, tape_rows())
    cut = tmp_path / "cut"
    write_tape(cut, [r for r in tape_rows() if r["t_recv_ms"] < T_D1])           # the tape ends 1 ms before the first t_d
    a = DR.collect_sets([DAY], tmp_path / "s1", full, start_ms=START, end_ms=T_D1 + 1)
    b = DR.collect_sets([DAY], tmp_path / "s2", cut, start_ms=START, end_ms=T_D1 + 1)
    assert a and [(t, [c["id"] for c in cs]) for t, cs in a] == [(t, [c["id"] for c in cs]) for t, cs in b]
    ra = [rr.request.request_sha256() for _, rr in DR.requests_for_set(a[0][0], a[0][1])]
    rb = [rr.request.request_sha256() for _, rr in DR.requests_for_set(b[0][0], b[0][1])]
    assert ra == rb                                                                 # identical requests with or without any later row
    for t, cards in a:
        assert all(c["as_of_ms"] == t for c in cards)
    with pytest.raises(RK.RankError):                                                # a card not built at t_d cannot be put in a request
        stale = dict(a[0][1][0], as_of_ms=a[0][0] - 1000)
        RK.build_rank_request(RK.iso_from_ms(a[0][0]), [stale] + a[0][1][1:])


def test_a_planted_future_print_is_refused_by_the_cards_the_dump_uses(tmp_path):
    from tools import hv4_cards as C
    import tools.hv7_runner as R

    write_tape(tmp_path / "tape", tape_rows())
    orig = R.Hv7Runner._card

    def planted(self, pool, T, snap, prints, feats):
        return orig(self, pool, T, snap, list(prints) + [(T + 5000, 1_000_000_000, True, "planted")], feats)

    R.Hv7Runner._card = planted
    try:
        assert DR.collect_sets([DAY], tmp_path / "s", tmp_path / "tape", start_ms=START, end_ms=END) == []         # every card refused: no set, no request
    finally:
        R.Hv7Runner._card = orig
    assert issubclass(C.LeakageError, Exception)


def test_output_path_and_inputs_are_refused(tmp_path):
    root = Path(DR.OUT_ROOT)
    root.mkdir()
    assert DR.check_out(root / "a") == root / "a"
    (tmp_path / "evil").mkdir()
    (root / "link").symlink_to(tmp_path / "evil")
    for bad in (tmp_path / "x", root, root.parent, str(root) + "/../x", str(root) + "x/y", "relative/out", root / "link" / "z", "/data/mal/arena/x", "/tmp/hv7-rehearsal/a"):
        with pytest.raises(DR.Refused):
            DR.check_out(bad)
    with pytest.raises(DR.Refused, match="tape_dir"):
        DR.run_dump([DAY], "d1", str(root / "a"), tape_dir=str(tmp_path))
    with pytest.raises(DR.Refused, match="out_not_under"):
        DR.run_dump(["2026-10-21"], "d1", str(tmp_path / "out"))
    with pytest.raises(DR.Refused, match="not_a_drill_root"):
        DR.run_dump(["2026-10-21"], "d1", str(root / "a"), scratch=tmp_path / "elsewhere")
    with pytest.raises(DR.Refused):
        DR.parse_days("2026-10-20")
    assert DR.main(["--drill-id", "d1", "--out", str(tmp_path / "nope")]) == 2
