"""Opt-in performance counters (VAO_PERF=1).

Standard library only, no Qt import, so the aggregation logic can be unit
tested on any platform (see tests/test_perf_stats.py).

How it is wired up:
  - main.py checks the VAO_PERF environment variable once at startup. When it
    is not "1", no PerfStats object exists and the hot paths (overlay paint,
    decay tick, audio event, capture chunk) pay a single `is not None` check.
  - When it is on, the overlay and the capture thread call the record_* methods
    and a 1 s timer in main.py calls PerfLog.flush(), which appends one summary
    line to perf.log next to settings.json.

Line format (one per flush, counts are for the interval since the last line):
  2026-11-10 14:03:07 secs=1.00 paints=0 paint_ms_mean=0.000 paint_ms_max=0.000
  ticks=0 events=0 dropped_hidden=0 chunks=20 chunk_ms_mean=0.310 chunk_ms_max=0.912
(printed on one line in the file)
"""

import os
import threading
import time

ENV_VAR = "VAO_PERF"
LOG_NAME = "perf.log"


def enabled_from_env(environ=None):
    """True only when VAO_PERF is exactly "1" (surrounding spaces ignored)."""
    if environ is None:
        environ = os.environ
    return environ.get(ENV_VAR, "").strip() == "1"


class _Timing:
    """Count, sum and max of durations in milliseconds."""

    __slots__ = ("count", "total", "max")

    def __init__(self):
        self.count = 0
        self.total = 0.0
        self.max = 0.0

    def add(self, ms):
        self.count += 1
        self.total += ms
        if ms > self.max:
            self.max = ms

    @property
    def mean(self):
        return self.total / self.count if self.count else 0.0


class PerfStats:
    """Counters for one reporting interval.

    Thread-safe: paints, ticks and audio events are recorded on the GUI thread,
    capture chunk times on the capture thread. The lock is uncontended almost
    always and only exists when VAO_PERF=1.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._reset()

    def _reset(self):
        self._paint = _Timing()
        self._chunk = _Timing()
        self._ticks = 0
        self._accepted = 0
        self._dropped = 0

    def record_paint(self, ms):
        with self._lock:
            self._paint.add(ms)

    def record_tick(self):
        with self._lock:
            self._ticks += 1

    def record_event(self):
        """An audio event reached a visible overlay."""
        with self._lock:
            self._accepted += 1

    def record_dropped(self):
        """An audio event was discarded because the overlay was hidden."""
        with self._lock:
            self._dropped += 1

    def record_chunk(self, ms):
        with self._lock:
            self._chunk.add(ms)

    def take(self):
        """Return the counters as a dict and start a new interval."""
        with self._lock:
            snap = {
                "paints": self._paint.count,
                "paint_ms_mean": self._paint.mean,
                "paint_ms_max": self._paint.max,
                "ticks": self._ticks,
                # Total received, so "events" minus "dropped_hidden" is what was
                # actually shown.
                "events": self._accepted + self._dropped,
                "dropped_hidden": self._dropped,
                "chunks": self._chunk.count,
                "chunk_ms_mean": self._chunk.mean,
                "chunk_ms_max": self._chunk.max,
            }
            self._reset()
        return snap


def format_line(snap, secs, wall_time):
    """One human-readable, grep-friendly log line (no trailing newline)."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wall_time))
    return (
        f"{stamp} secs={secs:.2f}"
        f" paints={snap['paints']}"
        f" paint_ms_mean={snap['paint_ms_mean']:.3f}"
        f" paint_ms_max={snap['paint_ms_max']:.3f}"
        f" ticks={snap['ticks']}"
        f" events={snap['events']}"
        f" dropped_hidden={snap['dropped_hidden']}"
        f" chunks={snap['chunks']}"
        f" chunk_ms_mean={snap['chunk_ms_mean']:.3f}"
        f" chunk_ms_max={snap['chunk_ms_max']:.3f}"
    )


class PerfLog:
    """Appends one summary line per flush() to a file.

    The file is opened once and kept open (line buffered) so a flush is a single
    small write. Each session starts with a '#' header line so several test runs
    can share one file and still be told apart.
    """

    def __init__(self, path, stats, clock=time.monotonic, wall_clock=time.time):
        self.path = path
        self.stats = stats
        self._clock = clock
        self._wall_clock = wall_clock
        self._last = clock()
        self._file = self._open(path)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wall_clock()))
        try:
            self._file.write(f"# VAO perf session started {stamp} pid={os.getpid()}\n")
        except OSError:
            # Don't leak the handle when the very first write fails.
            self.close()
            raise

    @staticmethod
    def _open(path):
        # Separate method so tests can substitute a fake file object.
        return open(path, "a", encoding="utf-8", buffering=1)

    def flush(self):
        """Write the counters gathered since the previous flush. Returns the
        line, or None once the log is closed or a write has failed."""
        if self._file is None:
            return None
        now = self._clock()
        secs = now - self._last
        self._last = now
        line = format_line(self.stats.take(), secs, self._wall_clock())
        try:
            self._file.write(line + "\n")
        except OSError:
            # Disk full, file locked by an editor, ...: stop logging rather than
            # raising out of a Qt timer slot.
            self.close()
            return None
        return line

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
