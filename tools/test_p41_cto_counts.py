import tools.p41_cto_counts as C

T0 = C.U("2026-09-20T00")


def tr(dt_h, q, venue="pumpswap"):
    return {"block_time": T0 + int(dt_h * 3600), "slot": 0, "event_index": 0, "venue": venue, "quote_reserve": int(q * 1e9)}


def ev(**k):
    e = {"sig": "s", "mint": "M", "blockTime": T0, "win": "C", "new_creator": "N", "is_holder_reward": None}
    e.update(k); return e


def test_labels_alive_and_up():
    rows = [tr(-1, 30), tr(0.5, 31), tr(23.5, 35), tr(24.5, 36)]
    r = C.label(ev(), rows, [], [{"type": "migration", "block_time": T0 - 7200}])
    assert r["trigger"] and r["venue_t"] == "pumpswap" and r["quote_t_sol"] == 30.0
    assert r["age_s"] == 7200 and r["age_left_censored"] is False
    assert r["alive24"] is True and r["q_up24"] is True and r["censored24"] is False


def test_censored_near_window_end():
    e = ev(blockTime=C.U("2026-09-24T12"))  # window C ends 09-25T07: t+25h is past it
    r = C.label(e, [], [], [])
    assert r["censored24"] and not r["trigger"] and "alive24" not in r


def test_dead_and_venue_change():
    r = C.label(ev(), [tr(-1, 30), tr(0.2, 20)], [], [])
    assert r["alive24"] is False and r["q_up24"] is False and r["age_left_censored"] is True
    r2 = C.label(ev(), [tr(-1, 40, "pump_bonding"), tr(2, 20)], [], [])
    assert r2["q_up24"] == "venue_changed"


def test_full_days_and_verdict():
    assert len(C.full_days("A")) == 13 and len(C.full_days("B")) == 11 and len(C.full_days("C")) == 6
    s = {"median_triggers_per_full_day": 3, "alive24_share": 0.5, "q_up24_share": 0.25, "depth_ok_share": 0.5}
    assert C.verdict(s, 30) == ("ADVANCE-TO-PRICING-DECLARE", [])
    assert C.verdict(dict(s, median_triggers_per_full_day=2.5), 30) == ("KILL", ["K0"])
    assert C.verdict(s, 4)[0] == "NOT_DECIDABLE"


def test_p_days_inside_windows():
    allx = C.full_days("B") + C.full_days("C")
    assert all(d in allx for d in C.P_DAYS) and len(C.P_DAYS) == 8


def test_streaming_reducer_matches_full_label(tmp_path):
    import json, random
    random.seed(1)
    rows = []
    for k in range(400):
        h = random.uniform(-30, 40)
        rows.append(dict(tr(h, random.uniform(5, 80), random.choice(["pumpswap", "pump_bonding"])), mint="M", slot=k, event_index=0))
    p = tmp_path / "trades.jsonl"
    random.shuffle(rows)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    e = ev(blockTime=T0, win="B")
    e["blockTime"] = C.U("2026-09-06T00")
    shift = e["blockTime"] - T0
    for r in rows: r["block_time"] += shift
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    full = C.label(e, sorted(rows, key=C._key), [], [])
    red = C.reduce_trades([e], str(p))[0]
    part = C.label(e, red["rows"], [], [])
    part["trades_1h_after"] = red["n1h"]
    assert part == full
