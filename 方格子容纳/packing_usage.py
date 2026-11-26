"""Read aggregate token counters emitted by one local Codex chat."""

import hashlib
import json
import math
import os
import threading
import time
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from uuid import UUID


_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
_ZERO = (0, 0, 0, 0)


def _counts(value):
    if isinstance(value, dict):
        result = tuple(value.get(key) for key in _FIELDS)
        if all(type(n) is int and n >= 0 for n in result):
            return result
    return None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.timestamp() if date.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


class _Rollout:
    def __init__(self, path):
        self.path = path
        self.identity = None
        self.offset = 0
        self.observed = None
        self.signature = None
        self.skipping = False
        self.valid = True
        self.times = []
        self.rows = []
        self.epoch = 0
        self.from_zero = False

    def _record(self, line, thread_id):
        first = self.offset == 0
        if not first and b'"token_count"' not in line and b'"session_meta"' not in line:
            return
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            self.valid = False
            return
        if not isinstance(row, dict):
            self.valid = False
            return
        payload = row.get("payload")
        if not isinstance(payload, dict):
            self.valid = not first
            return
        if first or row.get("type") == "session_meta":
            self.valid = row.get("type") == "session_meta" and payload.get("id") == thread_id
            return
        if row.get("type") != "event_msg" or payload.get("type") != "token_count":
            return
        info = payload.get("info")
        if info is None:  # This form reports only rate limits.
            return
        stamp = _timestamp(row.get("timestamp"))
        if stamp is None or (self.times and stamp < self.times[-1]):
            self.valid = False
            return
        totals = _counts(info.get("total_token_usage")) if isinstance(info, dict) else None
        if not self.rows:
            self.from_zero = totals is not None and totals == _counts(info.get("last_token_usage"))
        else:
            previous = self.rows[-1][1]
            if totals == previous:
                return
            if totals is None or previous is None or any(a < b for a, b in zip(totals, previous)):
                self.epoch += 1
        self.times.append(stamp)
        self.rows.append((self.epoch, totals))

    def _signature(self, stream):
        # Detect truncation followed by rapid regrowth under the original file identity.
        size = min(256, self.offset)
        stream.seek(0)
        digest = hashlib.blake2b(stream.read(size), digest_size=16)
        stream.seek(self.offset - size)
        digest.update(stream.read(size))
        return digest.digest()

    def refresh(self, thread_id):
        with self.path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            observed = (stat.st_size, stat.st_mtime_ns)
            if self.identity == identity and self.observed == observed:
                return
            if (self.identity != identity
                    or (self.observed and stat.st_size <= self.observed[0])
                    or self.signature != self._signature(stream)):
                self.__init__(self.path)
            self.identity, self.observed = identity, observed
            stream.seek(self.offset)
            # Never cache transcript content. Limit temporary buffers even for image records.
            while self.valid and self.offset < stat.st_size:
                limit = 8 * 1024**2 if self.offset == 0 else 128 * 1024
                line = stream.readline(limit)
                if self.skipping:
                    self.skipping = not line.endswith(b"\n")
                    if not line:
                        break
                elif not line.endswith(b"\n"):
                    if len(line) < limit:
                        break  # Writer will finish this record on a later poll.
                    if self.offset == 0 or b'"token_count"' in line[:256]:
                        self.valid = False
                        break
                    self.skipping = True
                else:
                    self._record(line, thread_id)
                self.offset = stream.tell()
            self.signature = self._signature(stream)

    def delta(self, start, end):
        i = bisect_right(self.times, end) - 1
        if not self.valid or i < 0:
            return None
        epoch, current = self.rows[i]
        j = bisect_left(self.times, start) - 1
        if j < 0:
            if not self.from_zero:
                return None
            baseline_epoch, baseline = 0, _ZERO
        else:
            baseline_epoch, baseline = self.rows[j]
        if baseline_epoch != epoch or current is None or baseline is None:
            return None
        return dict(zip(_FIELDS, (a - b for a, b in zip(current, baseline))))


class CodexUsage:
    """Thread-safe local sampler. Times are Unix seconds and both boundaries are inclusive."""

    def __init__(self, sessions_root: Path | None = None):
        base = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        self.root = Path(sessions_root) if sessions_root is not None else base / "sessions"
        self._lock = threading.Lock()
        self._rollouts = OrderedDict()

    def sample(self, thread_id: str, started: float, ended: float | None = None) -> dict | None:
        end = time.time() if ended is None else ended
        try:
            if (not isinstance(thread_id, str) or str(UUID(thread_id)) != thread_id or
                not math.isfinite(started) or not math.isfinite(end) or end < started):
                return None
        except (ValueError, TypeError, OverflowError):
            return None
        with self._lock:
            try:
                state = self._rollouts.get(thread_id)
                if state is None or not state.path.is_file():
                    matches = self.root.glob(f"????/??/??/rollout-*-{thread_id}.jsonl")
                    path = next(matches, None)
                    if (path is None or next(matches, None) is not None or
                        not path.resolve().is_relative_to(self.root.resolve())):
                        return None
                    state = self._rollouts[thread_id] = _Rollout(path)
                    if len(self._rollouts) > 8:
                        self._rollouts.popitem(last=False)
                self._rollouts.move_to_end(thread_id)
                state.refresh(thread_id)
                return state.delta(started, end)
            except OSError:
                return None
