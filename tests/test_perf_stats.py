"""Unit tests for perf_stats.py, the opt-in VAO_PERF counters.

Standard library only (no Qt, no audio), so they run on Linux CI.
"""

import threading

import pytest

from perf_stats import (ENV_VAR, PerfLog, PerfStats, enabled_from_env,
                        format_line)


# ── enabled_from_env ───────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("1", True),
    (" 1 ", True),
    ("0", False),
    ("", False),
    ("true", False),   # documented as exactly "1": no guessing
    ("yes", False),
])
def test_enabled_from_env(value, expected):
    assert enabled_from_env({ENV_VAR: value}) is expected


def test_disabled_when_unset():
    assert enabled_from_env({}) is False


# ── PerfStats ──────────────────────────────────────────────────────────

def test_empty_interval_is_all_zero():
    snap = PerfStats().take()
    assert snap == {
        "paints": 0, "paint_ms_mean": 0.0, "paint_ms_max": 0.0,
        "ticks": 0, "events": 0, "dropped_hidden": 0,
        "chunks": 0, "chunk_ms_mean": 0.0, "chunk_ms_max": 0.0,
    }


def test_counts_mean_and_max():
    s = PerfStats()
    for ms in (1.0, 3.0, 2.0):
        s.record_paint(ms)
    s.record_tick()
    s.record_tick()
    s.record_event()
    s.record_event()
    s.record_dropped()
    s.record_chunk(0.5)
    s.record_chunk(1.5)

    snap = s.take()
    assert snap["paints"] == 3
    assert snap["paint_ms_mean"] == pytest.approx(2.0)
    assert snap["paint_ms_max"] == pytest.approx(3.0)
    assert snap["ticks"] == 2
    assert snap["events"] == 3          # received = shown + dropped
    assert snap["dropped_hidden"] == 1
    assert snap["chunks"] == 2
    assert snap["chunk_ms_mean"] == pytest.approx(1.0)
    assert snap["chunk_ms_max"] == pytest.approx(1.5)


def test_take_starts_a_new_interval():
    s = PerfStats()
    s.record_paint(5.0)
    s.record_tick()
    s.take()
    snap = s.take()
    assert snap["paints"] == 0
    assert snap["paint_ms_max"] == 0.0
    assert snap["ticks"] == 0


def test_concurrent_recording_loses_nothing():
    # Paints arrive on the GUI thread while chunks arrive on the capture
    # thread; neither may lose counts or corrupt the other.
    s = PerfStats()
    n = 5000

    def chunks():
        for _ in range(n):
            s.record_chunk(0.1)

    def paints():
        for _ in range(n):
            s.record_paint(0.2)

    threads = [threading.Thread(target=chunks), threading.Thread(target=paints)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = s.take()
    assert snap["chunks"] == n
    assert snap["paints"] == n


# ── format_line / PerfLog ──────────────────────────────────────────────

def test_format_line_has_every_field():
    s = PerfStats()
    s.record_paint(1.25)
    line = format_line(s.take(), 1.0, 0.0)
    for key in ("secs=1.00", "paints=1", "paint_ms_mean=1.250",
                "paint_ms_max=1.250", "ticks=0", "events=0",
                "dropped_hidden=0", "chunks=0", "chunk_ms_mean=0.000",
                "chunk_ms_max=0.000"):
        assert key in line
    assert "\n" not in line


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_perflog_writes_header_and_one_line_per_flush(tmp_path):
    clock = FakeClock()
    stats = PerfStats()
    path = tmp_path / "perf.log"
    log = PerfLog(str(path), stats, clock=clock, wall_clock=lambda: 0.0)

    clock.t += 1.0
    stats.record_tick()
    first = log.flush()
    clock.t += 2.0
    second = log.flush()
    log.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# VAO perf session started")
    assert lines[1:] == [first, second]
    assert "secs=1.00" in first and "ticks=1" in first
    assert "secs=2.00" in second and "ticks=0" in second


def test_perflog_appends_sessions(tmp_path):
    path = tmp_path / "perf.log"
    for _ in range(2):
        log = PerfLog(str(path), PerfStats())
        log.flush()
        log.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert sum(1 for ln in lines if ln.startswith("#")) == 2
    assert len(lines) == 4


class FakeFile:
    """Stand-in for the log file: records writes, fails on demand."""

    def __init__(self, fail_on_write=None):
        self.fail_on_write = fail_on_write   # 1-based index of the failing write
        self.writes = []
        self.closed = False

    def write(self, text):
        if self.fail_on_write == len(self.writes) + 1:
            raise OSError("disk full")
        self.writes.append(text)

    def close(self):
        self.closed = True


def test_header_write_failure_closes_file_and_reraises(monkeypatch):
    fake = FakeFile(fail_on_write=1)
    monkeypatch.setattr(PerfLog, "_open", staticmethod(lambda path: fake))
    with pytest.raises(OSError):
        PerfLog("perf.log", PerfStats())
    assert fake.closed


def test_flush_after_write_error_returns_none_from_then_on(monkeypatch):
    fake = FakeFile(fail_on_write=2)            # header succeeds, first line fails
    monkeypatch.setattr(PerfLog, "_open", staticmethod(lambda path: fake))
    log = PerfLog("perf.log", PerfStats())
    assert log.flush() is None
    assert fake.closed
    assert log.flush() is None
    assert log.flush() is None
    assert len(fake.writes) == 1                # only the header got through


def test_flush_after_close_is_a_no_op(tmp_path):
    log = PerfLog(str(tmp_path / "perf.log"), PerfStats())
    log.close()
    assert log.flush() is None
    log.close()   # closing twice is fine too
