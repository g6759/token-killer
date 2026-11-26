"""Best-effort local snapshots of the official Codex account weekly window."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time


SOURCE = "Codex app-server account/rateLimits/read"
LOCAL_SOURCE = "local_configuration"
MIN_DISK_BYTES = 10 * 1024**3
MAX_TIMEOUT_SECONDS = 30
MAX_LINE_BYTES = 1024 * 1024


class WindowCounter:
    """Deduplicated, measured game tokens from completed model replies only."""

    def __init__(self):
        self._lock = threading.Lock()
        self._response_ids = set()
        self._uncached_input_tokens = 0
        self._output_tokens = 0

    def add(self, reply):
        """Count a valid response once; return whether it was newly counted."""
        if not isinstance(reply, dict):
            return False
        response_id = reply.get("response_id")
        usage = reply.get("usage")
        if not isinstance(response_id, str) or not response_id.strip() or not isinstance(usage, dict):
            return False
        fields = ("input_tokens", "cached_input_tokens", "output_tokens")
        if any(type(usage.get(key)) is not int or usage[key] < 0 for key in fields):
            return False
        if usage["cached_input_tokens"] > usage["input_tokens"]:
            return False
        with self._lock:
            if response_id in self._response_ids:
                return False
            self._response_ids.add(response_id)
            self._uncached_input_tokens += usage["input_tokens"] - usage["cached_input_tokens"]
            self._output_tokens += usage["output_tokens"]
            return True

    def snapshot(self):
        """Return an atomic counter copy, without storing response identifiers."""
        with self._lock:
            uncached = self._uncached_input_tokens
            output = self._output_tokens
            return {"response_count": len(self._response_ids),
                    "uncached_input_tokens": uncached,
                    "output_tokens": output,
                    "effective_game_tokens": uncached + output}


@contextmanager
def observe_window(start_monotonic, seconds, snapshot, emit, counter, *, interval=60):
    """Sample the shared account meter during one complete 600-second game.

    The caller supplies read-only ``snapshot`` and a thread-safe ``emit(record)``
    persistence callback. Both execute on one daemon sampling thread. No sample
    is taken for shorter windows; neither callback's failure affects the game.
    ``interval`` is injectable for a fast deterministic test; production uses 60.
    """
    if seconds != 600:
        yield counter
        return
    if not _number(start_monotonic) or not _number(interval) or interval <= 0:
        raise ValueError("invalid window sampling clock")
    if not isinstance(counter, WindowCounter) or not callable(snapshot) or not callable(emit):
        raise ValueError("window sampling requires counter and callbacks")
    stop = threading.Event()

    def sample():
        for index in range(1, 10):
            scheduled = start_monotonic + index * interval
            if stop.wait(max(0, scheduled - time.monotonic())):
                return
            if time.monotonic() >= start_monotonic + seconds:
                return
            # A delayed read must not create a burst of stale samples.
            if time.monotonic() - scheduled > interval / 2:
                continue
            try:
                account_snapshot = snapshot()
            except Exception as exc:
                account_snapshot = build_snapshot(None, error=type(exc).__name__)
            if stop.is_set() or time.monotonic() >= start_monotonic + seconds:
                return
            sampled = time.monotonic()
            record = {"scheduled_elapsed_seconds": index * interval,
                      "elapsed_seconds": round(sampled - start_monotonic, 3),
                      "sampled_at_utc": _timestamp(),
                      "receipt_counter": counter.snapshot(),
                      "account_snapshot": account_snapshot}
            try:
                emit(record)
            except Exception:
                # A meter log must never stop the game or later samples.
                pass

    worker = threading.Thread(target=sample, name="game-window-weekly-meter", daemon=True)
    worker.start()
    try:
        yield counter
    finally:
        stop.set()
        # collect() has its own deadline; never delay game teardown on it.
        worker.join(timeout=0.2)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _weekly_bucket(payload):
    if not isinstance(payload, dict):
        return None
    # Also accept the redacted shape returned by Codex Desktop get_usage_limits.
    buckets = payload.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(bucket, dict):
        codex = payload.get("codex")
        bucket = codex if isinstance(codex, dict) else None
    if not isinstance(bucket, dict):
        legacy = payload.get("rateLimits")
        bucket = legacy if isinstance(legacy, dict) and legacy.get("limitId") in (None, "codex") else None
    if not isinstance(bucket, dict):
        return None
    for window_name in ("primary", "secondary"):
        window = bucket.get(window_name)
        if isinstance(window, dict) and window.get("windowDurationMins") == 10080:
            used = window.get("usedPercent")
            if not _number(used) or not 0 <= used <= 100:
                return None
            reset = window.get("resetsAt")
            if type(reset) not in (int, float, str) or reset == "":
                reset = None
            return {
                "used_percent": used,
                "window_duration_minutes": 10080,
                "resets_at": reset,
            }
    return None


def build_snapshot(payload, *, app_weekly_limit_percent=None, sampled_at_utc=None,
                   source=SOURCE, error=None):
    """Build the minimal persistent record; app consumption is never inferred."""
    if app_weekly_limit_percent is not None and (
            not _number(app_weekly_limit_percent) or not 0 <= app_weekly_limit_percent <= 100):
        raise ValueError("app weekly limit must be a percentage from 0 through 100")
    week = _weekly_bucket(payload)
    if week is not None:
        week.update({"sampled_at_utc": sampled_at_utc or _timestamp(), "source": source})
    declaration = None
    if app_weekly_limit_percent is not None:
        declaration = {
            "weekly_limit_percent": app_weekly_limit_percent,
            "actual_used_percent": None,
            "source": LOCAL_SOURCE,
        }
    return {
        "codex_account_shared_weekly": week,
        "application_local_declaration": declaration,
        "collection_error": error,
    }


def compare_snapshots(before, after):
    """Compare account-shared percentage points only when reset window matches."""
    old = before.get("codex_account_shared_weekly") if isinstance(before, dict) else None
    new = after.get("codex_account_shared_weekly") if isinstance(after, dict) else None
    comparable = (
        isinstance(old, dict) and isinstance(new, dict)
        and old.get("window_duration_minutes") == new.get("window_duration_minutes") == 10080
        and old.get("resets_at") is not None and old.get("resets_at") == new.get("resets_at")
        and _number(old.get("used_percent")) and _number(new.get("used_percent"))
    )
    return {
        "comparable": bool(comparable),
        "percentage_point_change": (new["used_percent"] - old["used_percent"]) if comparable else None,
    }


def write_snapshot(path, snapshot):
    """Atomically persist a redacted snapshot to a caller-selected local path."""
    persisted = {
        "codex_account_shared_weekly": snapshot.get("codex_account_shared_weekly"),
        "application_local_declaration": snapshot.get("application_local_declaration"),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(persisted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def _read_app_server(timeout):
    executable = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if not executable:
        raise RuntimeError("codex_not_found")
    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout must be from 0 to 30 seconds")
    for location in (Path.cwd(), Path(__file__).resolve().parent, Path(tempfile.gettempdir())):
        if shutil.disk_usage(location).free < MIN_DISK_BYTES:
            raise RuntimeError("disk_below_10_gib")

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(Path(executable).resolve()), "app-server", "--listen", "stdio://"],
        cwd=tempfile.gettempdir(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, creationflags=creation_flags,
    )
    messages = queue.Queue()

    def read_stdout():
        try:
            for line in process.stdout:
                if len(line) > MAX_LINE_BYTES:
                    messages.put(None)
                    return
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(value, dict):
                    messages.put(value)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    request_id = 0

    def send(message):
        process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        process.stdin.flush()

    def request(method, params):
        nonlocal request_id
        request_id += 1
        ident = request_id
        send({"id": ident, "method": method, "params": params})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("app_server_timeout")
            try:
                message = messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("app_server_timeout") from None
            if message is None:
                raise RuntimeError("app_server_exited")
            if message.get("id") != ident:
                # Ignore ordinary server notifications; never answer server requests.
                if "id" in message and "method" in message:
                    raise RuntimeError("app_server_unexpected_request")
                continue
            result = message.get("result")
            if "error" in message or not isinstance(result, dict):
                raise RuntimeError("app_server_read_failed")
            return result

    try:
        request("initialize", {"clientInfo": {"name": "game_weekly_meter", "title": "Weekly meter", "version": "1.0"}})
        send({"method": "initialized"})
        return request("account/rateLimits/read", {})
    finally:
        if process.poll() is None:
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout):
            try:
                pipe.close()
            except OSError:
                pass
        reader.join(timeout=1)


def collect(*, app_weekly_limit_percent=None, timeout=12):
    """Read the official account weekly window; failures remain non-blocking."""
    try:
        payload = _read_app_server(timeout)
        snapshot = build_snapshot(payload, app_weekly_limit_percent=app_weekly_limit_percent)
        if snapshot["codex_account_shared_weekly"] is None:
            snapshot["collection_error"] = "weekly_window_unavailable"
        return snapshot
    except Exception as exc:
        # Error details may include server or environment data; persist only a class/code.
        reason = str(exc) if str(exc) in {"codex_not_found", "disk_below_10_gib", "app_server_timeout",
                                         "app_server_exited", "app_server_unexpected_request",
                                         "app_server_read_failed"} else type(exc).__name__
        return build_snapshot(None, app_weekly_limit_percent=app_weekly_limit_percent,
                              error=reason, sampled_at_utc=_timestamp())


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help="read and save one account snapshot")
    collect_parser.add_argument("--app-weekly-limit", type=float)
    collect_parser.add_argument("--timeout", type=float, default=12)
    collect_parser.add_argument("--output", type=Path)
    compare_parser = commands.add_parser("compare", help="compare two saved snapshots")
    compare_parser.add_argument("before", type=Path)
    compare_parser.add_argument("after", type=Path)
    args = parser.parse_args(argv)
    if args.command == "collect":
        snapshot = collect(app_weekly_limit_percent=args.app_weekly_limit, timeout=args.timeout)
        if args.output:
            write_snapshot(args.output, snapshot)
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(compare_snapshots(_load(args.before), _load(args.after)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
