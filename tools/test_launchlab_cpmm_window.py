"""launchlab_cpmm_fetch window mode (FLOOR-CPMM step 2): refusals, row drops, credit cap, old-mode regression. Fixtures only, no network."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from tools import launchlab_cpmm_fetch as C
from tools import launchlab_price as L
from tools import venue_backfill as vb


def ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


WIN = ts("2026-10-24T01:00:00Z")
RUN_END = ts("2026-11-21T20:00:00Z")


def test_win_start_constant():
    assert C.WIN_START == WIN and C.WIN_HIST_S == 225 * 3600


@pytest.mark.parametrize("bt", [ts("2026-10-23T23:59:59Z"), ts("2026-10-24T00:59:59Z"), ts("2026-10-17T00:00:00Z"), ts("2026-10-10T00:00:00Z")])
def test_migration_before_window_refused(bt):
    with pytest.raises(L.Refused):
        C.check_window_migration({"pool": "P", "bt": bt}, RUN_END)


def test_migration_at_window_start_accepted():
    C.check_window_migration({"pool": "P", "bt": WIN}, RUN_END)


def test_migration_at_or_after_run_end_refused():
    with pytest.raises(L.Refused):
        C.check_window_migration({"pool": "P", "bt": RUN_END}, RUN_END)


class Credits:
    def __init__(self, cap):
        self.cap, self.used = cap, 0

    def charge(self, n):
        if self.used + n > self.cap:
            raise vb.CapReached("cap")
        self.used += n


def tx(sig, bt):
    return {"transaction": {"signatures": [sig]}, "blockTime": bt, "slot": 1, "meta": {"logMessages": []}}


def test_row_drops_counted_before_window_and_after_run_end():
    t0 = WIN
    data = [tx("a", WIN - 1), tx("b", WIN - 7200), tx("c", WIN), tx("d", WIN + 60), tx("e", RUN_END), tx("f", RUN_END + 5)]
    calls = []

    def call(method, params):
        calls.append(params[1]["filters"]["blockTime"])
        return {"data": data, "paginationToken": None}

    rows, st = C.fetch_pool(call, Credits(10**6), "P", t0, t0 + C.WIN_HIST_S, end_ts=RUN_END, min_bt=C.WIN_START)
    assert st["dropped_before_window"] == 2 and st["dropped_outside"] == 2 and st["txs"] == 2 and st["covered_to_bt"] == min(t0 + C.WIN_HIST_S, RUN_END)
    assert calls[0] == {"gte": t0, "lt": min(t0 + C.WIN_HIST_S, RUN_END)}


def test_history_is_225_hours_or_run_end():
    seen = []

    def call(method, params):
        seen.append(params[1]["filters"]["blockTime"]["lt"])
        return {"data": [], "paginationToken": None}

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        C.process_window_migration(Path(d), call, Credits(10**6), {"pool": "P1", "bt": WIN}, RUN_END)
        C.process_window_migration(Path(d), call, Credits(10**6), {"pool": "P2", "bt": RUN_END - 3600}, RUN_END)
    assert seen == [WIN + 225 * 3600, RUN_END]


def test_run_window_refuses_whole_list_before_any_call(tmp_path):
    called = []

    def call(*a):
        called.append(a)
        return {"data": [], "paginationToken": None}

    migs = [{"pool": "ok", "bt": WIN + 10}, {"pool": "bad", "bt": WIN - 1}]
    with pytest.raises(L.Refused):
        C.run_window(tmp_path, call, Credits(10**6), migs, RUN_END, {})
    assert called == []


def test_credit_cap_stops_run_and_checkpoints(tmp_path):
    def call(method, params):
        return {"data": [tx("s", WIN + 5)], "paginationToken": "more"}  # never ends: each page costs 50

    cp = {}
    saved = []
    cr = Credits(120)
    rc = C.run_window(tmp_path, call, cr, [{"pool": "P", "bt": WIN}, {"pool": "Q", "bt": WIN + 1}], RUN_END, cp, lambda: saved.append(dict(cp)), max_pages=10)
    assert rc == C.EXIT_CAP and cr.used == 100 and cp["credits_used"] == 100 and saved and "P" not in cp.get("window", {})


def test_run_window_writes_pool_file_and_skips_done(tmp_path):
    n = []

    def call(method, params):
        n.append(1)
        return {"data": [tx("s", WIN + 5)], "paginationToken": None}

    cp = {}
    migs = [{"pool": "P", "bt": WIN, "mint0": "M0"}]
    assert C.run_window(tmp_path, call, Credits(10**6), migs, RUN_END, cp) == 0
    assert cp["window"]["P"]["status"] == "ok" and cp["window"]["P"]["credits"] == C.COST_GTFA
    assert C.run_window(tmp_path, call, Credits(10**6), migs, RUN_END, cp) == 0
    assert len(n) == 1  # second run skips the finished pool
    assert any((tmp_path / "pools").glob("pool-P.jsonl*"))


def test_load_migrations_plain_list_and_step1b_shape(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps([{"pool": "B", "bt": WIN + 5}, {"pool": "A", "bt": WIN}, {"pool": "A", "bt": WIN}, {"cpmm_pool": "C", "block_time": WIN + 9, "vault0": "v"}]))
    got = C.load_window_migrations(str(f))
    assert [m["pool"] for m in got] == ["A", "B", "C"] and got[2]["vault0"] == "v"
    f.write_text(json.dumps([{"pool": "X"}]))
    with pytest.raises(L.Refused):
        C.load_window_migrations(str(f))


def test_main_window_refuses_rps_over_8_and_missing_list(capsys, tmp_path):
    base = ["window", "--run-end", "2026-11-21T20:00:00Z", "--out-dir", str(tmp_path / "o")]
    assert C.main(base + ["--rps", "9", "--migrations", "x"]) == 2
    assert C.main(base) == 2


def test_main_window_requires_run_end(tmp_path, capsys):
    assert C.main(["window", "--migrations", "x", "--out-dir", str(tmp_path / "o")]) == 2
    assert "--run-end" in capsys.readouterr().err


def test_main_window_requires_out_dir(capsys):
    assert C.main(["window", "--migrations", "x", "--run-end", "2026-11-21T20:00:00Z"]) == 2
    assert "--out-dir" in capsys.readouterr().err


@pytest.mark.parametrize("d", ["/data2/mal/venue-tape-backfill/launchlab_cpmm", "/data2/mal/venue-tape-backfill/launchlab_cpmm/pools", "/data2/mal/venue-tape-backfill",
                               "/data2/mal/venue-tape-backfill/launchlab", "/data2/mal/venue-tape-backfill/x/../launchlab_cpmm", "/data/mal/arena/tape", "/data/mal/blocks/forward-1016",
                               "/var/lib/mal/sealed/fast-trades-tip"])
def test_window_out_dir_under_sealed_root_refused(d):
    with pytest.raises(L.Refused):
        C.check_window_out_dir(Path(d))
    with pytest.raises(L.Refused):  # main refuses before the key is read or any call is made
        C.main(["window", "--migrations", "x", "--run-end", "2026-11-21T20:00:00Z", "--out-dir", d])


def test_window_out_dir_fresh_ok_and_other_checkpoint_refused(tmp_path):
    C.check_window_out_dir(tmp_path / "fresh")
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "checkpoint.json").write_text("{}")
    with pytest.raises(L.Refused):
        C.check_window_out_dir(tmp_path / "old")


def test_window_uses_its_own_checkpoint_name():
    assert C.WINDOW_CP != "checkpoint.json"


def test_old_mode_unchanged_defaults():
    end = int(L.EXPLORE_END.timestamp())
    t0 = end - 1000

    def call(method, params):
        return {"data": [tx("a", t0 + 10), tx("b", end + 1), tx("c", t0 - 5)], "paginationToken": None}

    rows, st = C.fetch_pool(call, Credits(10**6), "P", t0, t0 + 86400)
    assert "dropped_before_window" not in st and st["dropped_outside"] == 2 and st["txs"] == 1
