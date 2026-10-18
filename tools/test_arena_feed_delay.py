"""arena_feed_delay: sampling, rate limit, the delay arithmetic, the JSON, URL and path guards. No network: the RPC is a fake."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess

import numpy as np
import pytest

from tools import arena_feed_delay as D


def make_tape(d, n_slots=15_100, delay_ms=1_200, hours=("2026-10-18T03",), extra_rows=True):
    """Trade rows with one slot every 400 ms; t_recv = true slot time + delay. Block time (whole seconds) = floor(0.4 * slot)."""
    d.mkdir(parents=True, exist_ok=True)
    per = n_slots // len(hours)
    s = 0
    for h in hours:
        with open(d / f"tape-{h}.jsonl", "w") as f:
            for _ in range(per):
                f.write(json.dumps({"v": 1, "type": "trade", "prog": "pumpswap", "slot": 10_000 + s, "t_recv_ms": 1_789_000_000_000 + s * 400 + delay_ms,
                                    "sol_lamports": 123, "quote_reserve": 5, "trader": "x"}) + "\n")
                if extra_rows and s % 7 == 0:  # a later duplicate receive of the same slot and a non-trade row: both must be ignored / min-reduced
                    f.write(json.dumps({"v": 1, "type": "trade", "prog": "pumpfun", "slot": 10_000 + s, "t_recv_ms": 1_789_000_000_000 + s * 400 + delay_ms + 900}) + "\n")
                    f.write(json.dumps({"v": 1, "type": "hb", "prog": "pumpswap", "slot": 10_000 + s, "t_recv_ms": 5}) + "\n")
                s += 1
    return d


def fake_block_time(slot):
    return 1_789_000_000 + ((slot - 10_000) * 2) // 5  # floor(0.4 * slot) seconds after the epoch of the fake chain


class Clock:
    def __init__(self):
        self.t, self.calls = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_sampling_is_every_50th_slot_with_at_least_300():
    slots = list(range(15_100))
    s, st = D.sample_slots(slots)
    assert st == 50 and len(s) == 302 and s[:3] == [0, 50, 100]
    s, st = D.sample_slots(list(range(14_999)))  # the stride shrinks to keep 300
    assert st == 49 and len(s) >= 300
    s, st = D.sample_slots(list(range(300)))
    assert st == 1 and len(s) == 300
    with pytest.raises(D.Refused, match="only 299 distinct slots"):
        D.sample_slots(list(range(299)))


def test_requests_are_limited_to_two_per_second():
    clock, stamps = Clock(), []
    lim = D.RateLimiter(2.0, clock.now, clock.sleep)
    for _ in range(10):
        lim.wait()
        stamps.append(clock.t)
    gaps = np.diff(stamps)
    assert gaps.min() >= 0.5 - 1e-9 and gaps.max() <= 0.5 + 1e-9
    with pytest.raises(D.Refused):
        D.RateLimiter(2.5)
    with pytest.raises(D.Refused):
        D.RateLimiter(0)


def test_measure_computes_median_p90_seconds_per_slot_and_d(tmp_path):
    tape = make_tape(tmp_path / "tape")
    clock = Clock()
    res = D.measure(tape, fake_block_time, D.RateLimiter(2.0, clock.now, clock.sleep))
    assert res["sampled"] == 302 and res["usable"] == 302 and res["missing"] == 0 and res["stride"] == 50
    assert clock.t >= 0.5 * 301 - 1e-6  # 302 requests at 2 per second took at least 150 s of (fake) time
    # independent arithmetic
    smp = res["samples"]
    delay = np.array([(x["t_recv_ms"] - x["block_time"] * 1000) / 1000 for x in smp])
    assert res["median_delay_s"] == pytest.approx(float(np.median(delay))) and res["p90_delay_s"] == pytest.approx(float(np.percentile(delay, 90)))
    assert 1.2 <= res["median_delay_s"] <= 2.3  # 1.2 s true delay + up to 1 s of block_time truncation
    assert res["seconds_per_slot"] == pytest.approx(0.4, abs=0.01)
    assert res["feed_delay_slots"] == math.ceil(res["median_delay_s"] / res["seconds_per_slot"] - 1e-9)
    assert res["feed_delay_slots"] >= 3
    # timing only: a sample carries three numbers and nothing from the trade bodies
    assert set(smp[0]) == {"slot", "t_recv_ms", "block_time"}
    assert "sol_lamports" not in json.dumps(res) and "quote_reserve" not in json.dumps(res)


def test_the_first_receive_of_a_slot_counts_and_non_trade_rows_are_ignored(tmp_path):
    tape = make_tape(tmp_path / "tape", n_slots=300)
    first = D.first_receive_by_slot(D.tape_files(tape))
    assert len(first) == 300 and first[10_000] == 1_789_000_000_000 + 1_200  # the +900 ms duplicate on slot 10,000 and the hb row did not move it


def test_json_lists_the_tape_files_as_path_at_sha256(tmp_path, monkeypatch):
    tape = make_tape(tmp_path / "tape", hours=("2026-10-18T03", "2026-10-18T04"))
    monkeypatch.setattr(D, "rpc_block_time", lambda url, slot, *a, **k: fake_block_time(slot))
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = tmp_path / "out" / "fd.json"
    assert D.main(["--tape-dir", str(tape), "--out", str(out), "--last-hours", "2"]) == 0
    doc = json.loads(out.read_text())
    assert len(doc["tapes"]) == 2
    for entry in doc["tapes"]:
        path, sha = entry.rsplit("@", 1)
        assert hashlib.sha256(open(path, "rb").read()).hexdigest() == sha
    assert doc["rpc_host"] == "api.mainnet-beta.solana.com" and doc["feed_delay_slots"] >= 3 and "generated_ms" in doc


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI not installed")
def test_a_compacted_zst_hour_is_read_and_preferred_to_its_jsonl(tmp_path):
    tape = make_tape(tmp_path / "tape", n_slots=400)
    src = next(tape.glob("tape-*.jsonl"))
    subprocess.run(["zstd", "-q", str(src), "-o", str(src) + ".zst"], check=True)
    files = D.tape_files(tape)
    assert [f.name for f in files] == [src.name + ".zst"]
    assert len(D.first_receive_by_slot(files)) == 400


def test_missing_blocks_and_too_few_samples_are_refused(tmp_path):
    tape = make_tape(tmp_path / "tape")
    clock = Clock()
    skipped = lambda slot: None if (slot // 50) % 5 == 0 else fake_block_time(slot)  # noqa: E731
    with pytest.raises(D.Refused, match="no block time"):
        D.measure(tape, skipped, D.RateLimiter(2.0, clock.now, clock.sleep))
    few = make_tape(tmp_path / "few", n_slots=200)
    with pytest.raises(D.Refused, match="only 200 distinct slots"):
        D.measure(few, fake_block_time, D.RateLimiter(2.0, clock.now, clock.sleep))
    with pytest.raises(D.Refused, match="no tape"):
        (tmp_path / "empty").mkdir()
        D.measure(tmp_path / "empty", fake_block_time)


def test_rpc_url_and_tape_path_guards(tmp_path):
    assert D.check_rpc_url("https://api.mainnet-beta.solana.com") and D.check_rpc_url("https://api.mainnet-beta.solana.com/")
    for bad in ("http://api.mainnet-beta.solana.com", "https://mainnet.helius-rpc.com", "https://x.example.com/?api-key=abc", "https://user:pw@x.example.com",
                "https://x.example.com/v2/abcdef", "ftp://x"):
        with pytest.raises(D.Refused):
            D.check_rpc_url(bad)
    for part in ("sealed", "forward-1016", "shadow", "exp012-forward", "tip-tape-archive"):
        p = tmp_path / part / "tape"
        p.mkdir(parents=True)
        with pytest.raises(D.Refused, match="not an arena tape path"):
            D.check_tape_dir(p)
    assert D.main(["--tape-dir", str(tmp_path), "--out", str(tmp_path / "o.json"), "--rpc-url", "https://mainnet.helius-rpc.com"]) == 2
