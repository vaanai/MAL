from tools import p29_keeper_pager as p
from tools import p29_keeper_counts as c


def test_classify():
    assert p.classify("[46] pump-limit:v3:stop_loss:55567:1000000000000000; [8] bypass:0") == ("SL", "3")
    assert p.classify("pump-limit:v3:take_profit:256654:1") == ("TP", "3")
    assert p.classify("pump-limit:v3:weird:1") == ("OT", "3")
    assert p.classify(None) == ("", "")
    assert p.classify("hello") == ("", "")


def test_fill_slots_and_cluster():
    rows = [
        {"sig": "c1", "slot": 10, "bt": 100, "err": 0, "kind": "SL", "ver": "3"},
        {"sig": "f1", "slot": 10, "bt": 100, "err": 0, "kind": "", "ver": ""},
        {"sig": "c2", "slot": 11, "bt": 101, "err": 0, "kind": "TP", "ver": "3"},
        {"sig": "f2", "slot": 11, "bt": 101, "err": 0, "kind": "", "ver": ""},
        {"sig": "x", "slot": 50, "bt": 150, "err": 0, "kind": "", "ver": ""},  # no create in slot
        {"sig": "f3", "slot": 10, "bt": 100, "err": 1, "kind": "", "ver": ""},  # failed
    ]
    fills = c.fill_map(rows)
    assert fills == {"f1": "SL", "f2": "TP"}
    # cluster: 3 tagged within 60 s -> one event, then refractory
    ev = [100, 110, 120, 130, 140, 400, 410, 420]
    assert c.cluster_times(ev, 3, 60, 60) == [120, 420]
    assert c.impact_ok(30e9, 0.0) and not c.impact_ok(10e9, 0.0)
    assert c.impact_ok(10e9, 17.584e9)


def test_coverage_distinct_and_allowlist():
    from tools import p29_keeper_coverage as cv
    # same signature three times is ONE distinct fill: no event; three distinct sigs: one event
    assert cv.events_distinct([(1, "a"), (2, "a"), (3, "a")]) == []
    assert cv.events_distinct([(1, "a"), (2, "b"), (3, "c"), (4, "d")]) == [3]
    forbidden = ("probe", "shadow", "canary", "forward-", "fresh-08", "truth-", "tip-tape", "sealed", "arena", "meta-capture")
    for label, role, dirs, days in cv.BLOCKS:
        assert all(not any(f in d for f in forbidden) for d in dirs)
        assert all("2026-09-04" <= d <= "2026-09-24" for d in days)
