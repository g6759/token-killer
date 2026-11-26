"""Store continuous-session window references and recover confirmed metering."""

from datetime import datetime, timezone
import re

from benchmark import WINDOWS, metrics
from workspace_io import read, save


FORMAT = "research-games-continuous/v1"
WINDOW_NAME = re.compile(r"吞吐对照-混合冲榜-\d{8}-\d{6}-\d{6}")
COUNTERS = ("uncached_input_tokens", "output_tokens", "metered_rounds", "project_records")


def new_history_path(results):
    folder = results / "连续冲榜"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".json")


def write_history(path, scenario, window_limit, windows, *, end_reason=None, message=None):
    save(path, {"format": FORMAT, "scenario": scenario, "window_limit": window_limit,
                "windows": [folder.name for folder in windows], "end_reason": end_reason, "message": message})


def _read_mapping(path):
    try:
        value = read(path)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _confirmed_receipts(group):
    timing = _read_mapping(group / "window.json")
    seconds = timing.get("seconds")
    if not timing.get("live") or type(seconds) is not int or seconds <= 0:
        return {}
    receipts = {}
    for path in group.glob("lane-*/[0-9]*.json"):
        row = _read_mapping(path)
        response = row.get("response_id")
        if row.get("in_window") is True and isinstance(response, str) and response:
            receipts.setdefault(response, row)
    for path in group.glob("lane-*/pending-*.json"):
        row = _read_mapping(path)
        elapsed = row.get("received_elapsed")
        reply = row.get("reply")
        if (row.get("state") not in ("received", "verified") or not isinstance(reply, dict)
                or type(elapsed) not in (int, float) or not 0 <= elapsed <= seconds):
            continue
        response = reply.get("response_id")
        if isinstance(response, str) and response:
            receipts.setdefault(response, {"usage": reply.get("usage")})
    return metrics(list(receipts.values()), seconds, live=True)


def _window_summary(folder, scenario):
    group = folder / scenario
    window = _read_mapping(group / "summary.json")
    if not window:
        report = _read_mapping(folder / "summary.json")
        windows = report.get("scenarios")
        window = windows.get(scenario, {}) if isinstance(windows, dict) and report.get("live") else {}
    if (isinstance(window, dict) and window.get("live")
            and all(type(window.get(key)) is int and window[key] >= 0 for key in COUNTERS)):
        return window
    return _confirmed_receipts(group)

def window_rate(entry):
    """Receipt rate for the most recently measured window, including a partial stop."""
    if not entry.get("metered_rounds"):
        return None
    if entry.get("stop") == "window":
        return entry.get("tokens_per_minute")
    seconds = entry.get("wall_seconds")
    if type(seconds) not in (int, float) or seconds <= 0:
        return None
    return 60 * (entry.get("uncached_input_tokens", 0) + entry.get("output_tokens", 0)) / seconds


def latest_history(results):
    for path in sorted((results / "连续冲榜").glob("*.json"), reverse=True):
        manifest = _read_mapping(path)
        scenario, names = manifest.get("scenario"), manifest.get("windows")
        if (manifest.get("format") != FORMAT or not isinstance(scenario, str) or scenario not in WINDOWS
                or not isinstance(names, list)
                or not all(isinstance(name, str) and WINDOW_NAME.fullmatch(name) for name in names)
                or len(set(names)) != len(names)):
            continue
        root = results.resolve()
        folders = [(root / name).resolve() for name in names]
        if any(folder.parent != root for folder in folders):
            continue
        windows = [_window_summary(folder, scenario) for folder in folders]
        complete = sum(window.get("stop") == "window" and not window.get("errors")
                       and window.get("comparable", True) and window["metered_rounds"] > 0
                       for window in windows)
        message = manifest.get("message") or next(
            (error for window in reversed(windows) for error in window.get("errors", ())), None)
        return {"scenario": scenario, "window_limit": manifest.get("window_limit"), "window": len(names),
                "completed": complete,
                "last_window_rate": next((window_rate(w) for w in reversed(windows) if w.get("metered_rounds")), None),
                "effective_tokens": sum(w.get("uncached_input_tokens", 0) + w.get("output_tokens", 0)
                                        for w in windows),
                "metered_rounds": sum(w.get("metered_rounds", 0) for w in windows),
                "new_records": sum(w.get("project_records", 0) for w in windows),
                "end_reason": manifest.get("end_reason") or "recovered", "message": message}
    return None
